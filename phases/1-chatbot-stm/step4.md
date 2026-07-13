# Step 4: stm

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/PRD.md`
- `/docs/ARCHITECTURE.md` (특히 "상태 관리", "데이터 흐름 (1단계)" 섹션)
- `/docs/ADR.md` (특히 ADR-002: 함수/변수 이름은 기본적으로 원본을 따르되 이름이 나쁠 때만 바꾼다)
- `lossy_clone/__init__.py`, `lossy_clone/memory/__init__.py` — Step 0에서 생성된 폴더 뼈대
- `lossy_clone/llm.py` — Step 2, 3에서 생성된 LLM 연동 (이 step은 이걸 직접 쓰진 않지만, 같은 패키지 컨벤션과 import 규칙을 따라야 한다)
- `lossy_clone/tests/conftest.py` — Step 1의 import 충돌 가드. 자동 적용되므로 `lossy_clone.` 접두사 또는 상대 import만 사용하라.

## 작업

`lossy_clone/memory/stm.py`를 새로 만든다. STM(Short-Term Memory)은 세션 내 최근 대화 메시지를 로컬 SQLite에 저장/조회하는 계층이다.

### 스키마

ADR-002에 따라 원본의 필드명을 그대로 따른다 (이름이 나쁘지 않으므로 바꿀 이유가 없다). 테이블명 `stm_messages`, 필드: `id`(UUID PK), `session_id`, `role`(`user`/`assistant`/`system`), `content`, `timestamp`(ISO 8601), `turn_index`(세션 내 0부터 시작하는 순번). 단, 코드는 원본을 복사하지 않고 스스로 새로 작성한다.

### 인터페이스

```python
import sqlite3
from pathlib import Path

def init_stm(conn: sqlite3.Connection) -> None:
    """stm_messages 테이블과 인덱스를 생성한다 (이미 있으면 아무 것도 하지 않음, 멱등)."""
    ...

def add_message(conn: sqlite3.Connection, session_id: str, role: str, content: str) -> dict:
    """새 메시지를 저장하고 저장된 row를 dict로 반환한다.
    turn_index는 해당 session_id 내에서 자동으로 다음 순번을 계산한다."""
    ...

def get_recent_messages(conn: sqlite3.Connection, session_id: str, limit: int = 20) -> list[dict]:
    """해당 session_id의 최근 메시지를 turn_index 오름차순(대화 순서)으로 최대 limit개 반환한다."""
    ...
```

### 핵심 규칙 (반드시 지켜야 함)

- **DB 경로는 호출자가 넘긴 `sqlite3.Connection`을 그대로 사용한다.** `stm.py` 자체는 DB 파일 경로를 하드코딩하지 않는다 (경로 결정은 Step 5의 `Chatbot`이 담당). 단, 이 원칙을 지키기 위해 `sqlite3.connect`를 이 모듈 안에서 직접 호출하지 마라.
- `add_message`는 같은 `session_id` 안에서 `turn_index`가 반드시 0부터 순차적으로 증가해야 한다 (동시성은 고려하지 않아도 되지만, 순차 호출 시 절대 건너뛰거나 중복되면 안 된다).
- `role`은 `user`/`assistant`/`system` 중 하나가 아니면 명확한 예외를 던진다.
- `timestamp`는 UTC 기준 ISO 8601 문자열로 저장한다.

### 테스트

`lossy_clone/tests/test_stm.py`를 먼저 작성하고 통과하는 구현을 만들어라 (TDD). `sqlite3.connect(":memory:")`를 사용해 파일 시스템에 의존하지 않는 테스트로 작성한다.

- `init_stm()` 호출 후 테이블이 생성되고, 두 번 호출해도 에러 없이 멱등한지.
- `add_message()`로 여러 메시지를 추가하면 `turn_index`가 0, 1, 2...로 순차 증가하는지.
- 서로 다른 `session_id`는 `turn_index`가 독립적으로 0부터 시작하는지 (세션 격리).
- `get_recent_messages(limit=N)`이 최근 N개를 대화 순서(오래된 것 → 최신)로 반환하는지, `N`보다 메시지가 적을 때도 정상 동작하는지.
- 잘못된 `role` 값을 넣으면 예외가 발생하는지.

## Acceptance Criteria

```bash
python -m compileall -q .   # 구문 오류 없음
python -m pytest            # 테스트 통과
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `ARCHITECTURE.md`의 상태 관리 원칙(로컬 SQLite, 필드명 유지)을 따르는가?
   - `ADR.md`(ADR-002 이름 유지, ADR-003 STM만 다루고 LTM/Episodic은 아직 안 만듦)를 벗어나지 않았는가?
   - `CLAUDE.md` CRITICAL 규칙(`lossy_clone/` 밖 파일 미참조)을 위반하지 않았는가?
3. 결과에 따라 `phases/1-chatbot-stm/index.json`의 `step 4` 항목을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary"`에 생성한 파일과 스키마 핵심 사항을 한 줄로 요약
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `stm.py` 안에서 `sqlite3.connect()`를 직접 호출해 DB 파일 경로를 결정하지 마라. 이유: DB 경로 결정은 Step 5의 `Chatbot`이 패키지 위치 기준으로 계산하는 책임이며, `stm.py`가 경로를 하드코딩하면 재사용성과 테스트 격리가 깨진다.
- LTM, Episodic 관련 테이블/함수를 만들지 마라. 이 step은 STM만 다룬다 (ADR-003, 2/3단계는 아직 범위 밖).
- 저장소 루트의 `memory/stm.py`(원본)를 import하거나 그대로 복사하지 마라. 개념(필드 구성)만 참고하고 코드는 새로 작성한다 (ADR-001, ADR-002).
- `import memory`처럼 접두사 없는 절대 import를 쓰지 마라. 반드시 `lossy_clone.memory.stm` 또는 패키지 내부 상대 import를 사용하라.
- 기존 테스트를 깨뜨리지 마라.
