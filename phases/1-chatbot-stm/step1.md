# Step 1: import-guard

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/PRD.md` (특히 저장소 루트에 `chatbot.py`, `memory/`, `episodic_schema.py`로 이루어진 원본 프로젝트가 이미 있다는 배경)
- `/docs/ADR.md` (ADR-001: `lossy_clone/` 밖의 어떤 파일도 import/참조하지 않는다)
- `lossy_clone/__init__.py`, `lossy_clone/tests/__init__.py` — Step 0에서 생성된 폴더 뼈대

## 배경

저장소 루트에는 `lossy_clone`이 재구현 대상으로 참고만 하는 원본 프로젝트가 그대로 남아 있고, 그 루트에는 `chatbot.py` 파일과 `memory/` 패키지가 존재한다 (`docs/PRD.md` 참고). `lossy_clone/` 안에도 앞으로 같은 이름의 `chatbot.py`, `memory/` 서브패키지가 생긴다 (Step 4, 5).

`python -m pytest`는 저장소 루트에서 실행되므로, 만약 `lossy_clone` 안의 어떤 코드가 실수로 접두사 없는 `import chatbot`이나 `import memory`를 쓰면, `sys.path` 구성상 `lossy_clone/`의 것이 아니라 **저장소 루트의 원본 모듈이 조용히 import**되어 ADR-001(독립성)을 위반한 채로 테스트가 통과해버릴 수 있다. 이 step은 그 상황을 자동으로 감지해 테스트를 실패시키는 가드를 추가한다.

## 작업

`lossy_clone/tests/conftest.py`를 새로 만든다.

### 가드 로직

```python
import sys
from pathlib import Path

def pytest_collection_modifyitems(session, config, items):
    """테스트 수집이 끝난 시점에 'chatbot'/'memory' 이름으로 import된 모듈이
    lossy_clone/ 밖(=저장소 루트의 원본)의 것이면 즉시 테스트 세션을 실패시킨다."""
    ...
```

- 대상은 정확히 `"chatbot"`, `"memory"` 두 이름이다 (bare import 시 저장소 루트 원본과 충돌하는 이름).
- `sys.modules.get(name)`으로 조회하고, 없으면(`None`) 넘어간다 (아직 아무도 그 이름으로 import하지 않은 정상 상태).
- 모듈이 있으면 `getattr(module, "__file__", None)` 또는 (패키지라면) `module.__path__[0]`으로 실제 파일 경로를 얻는다.
- `lossy_clone` 패키지 디렉토리는 `Path(__file__).resolve().parent.parent`로 계산한다 (`conftest.py`가 `lossy_clone/tests/` 안에 있으므로 두 단계 위가 `lossy_clone/`).
- 얻은 모듈 경로가 `lossy_clone` 디렉토리 하위가 아니면, 어떤 모듈이 어디서 잘못 import됐는지 명시한 에러 메시지와 함께 `RuntimeError`를 발생시켜라 (pytest가 collection 단계 실패로 처리한다).

### 테스트

`lossy_clone/tests/test_import_guard.py`를 먼저 작성하고 통과하는 구현을 만들어라 (TDD). `chatbot.py`/`memory/stm.py`가 아직 없어도(Step 4, 5 이전이어도) 이 가드 자체는 `sys.modules`를 직접 조작해서 독립적으로 테스트할 수 있다.

- **위반 케이스**: `sys.modules["chatbot"]`에 `__file__`이 `lossy_clone/` 밖의 경로(예: 저장소 루트의 실제 `chatbot.py` 경로, 또는 임의의 `/tmp` 경로)를 가리키는 가짜 모듈 객체(`types.ModuleType` 등으로 생성)를 넣고 `pytest_collection_modifyitems(session=None, config=None, items=[])`를 호출하면 `RuntimeError`가 발생하는지 확인한다.
- **정상 케이스**: `__file__`이 `lossy_clone/` 안의 경로를 가리키는 가짜 모듈을 넣으면 예외가 발생하지 않는지 확인한다.
- **테스트 격리**: 각 테스트는 `sys.modules`를 조작하기 전 원래 상태를 저장했다가 `try/finally`(또는 pytest fixture의 teardown)로 반드시 원상복구해서, 이 테스트가 다른 테스트에 영향을 주지 않게 하라. 이유: `sys.modules`는 프로세스 전역 상태라서 정리하지 않으면 이후 테스트나 이후 step의 테스트 결과를 오염시킬 수 있다.

## Acceptance Criteria

```bash
python -m compileall -q .   # 구문 오류 없음
python -m pytest            # 테스트 통과
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `ADR.md`(ADR-001 독립성)를 벗어나지 않았는가?
   - `CLAUDE.md` CRITICAL 규칙(`lossy_clone/` 밖 파일 미참조)을 위반하지 않았는가?
3. 결과에 따라 `phases/1-chatbot-stm/index.json`의 `step 1` 항목을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary"`에 가드 동작 방식을 한 줄로 요약
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `chatbot.py`, `memory/stm.py` 등 실제 기능 모듈을 이 step에서 만들지 마라. 이 step은 가드 로직 하나만 다룬다 (Step 4, 5에서 실제 모듈을 만든다).
- 테스트에서 `sys.modules`를 조작한 뒤 정리하지 않는 코드를 작성하지 마라. 이유: 위 "테스트 격리" 항목 참고 — 이후 step들의 테스트가 오염될 수 있다.
- 저장소 루트의 원본 `chatbot.py`/`memory/`를 실제로 import해서 테스트하지 마라 (가짜 모듈 객체로 시뮬레이션하면 충분하다). 이유: 원본을 실제로 import하는 코드 자체가 ADR-001이 막으려는 상황이다.
- 기존 테스트를 깨뜨리지 마라.
