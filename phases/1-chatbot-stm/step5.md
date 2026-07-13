# Step 5: chatbot

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/PRD.md`
- `/docs/ARCHITECTURE.md` (특히 "데이터 흐름 (1단계)" 섹션 — 이 step이 구현하는 흐름 그 자체)
- `/docs/ADR.md`
- `lossy_clone/llm.py` — Step 2, 3에서 만든 `LLMClient`/`GeminiLLMClient`/`FakeLLMClient`
- `lossy_clone/memory/stm.py` — Step 4에서 만든 `init_stm`/`add_message`/`get_recent_messages`
- `lossy_clone/tests/conftest.py` — Step 1의 import 충돌 가드
- `lossy_clone/README.md` — Step 0에서 만든 스텁 (이 step에서 사용 예시 섹션을 채운다)

이전 step에서 만들어진 `llm.py`, `memory/stm.py`의 실제 함수/클래스 시그니처를 코드에서 직접 확인한 뒤 작업하라 (이 문서에 적힌 시그니처와 실제 구현이 미묘하게 다를 수 있으므로 실제 코드를 신뢰하라).

## 작업

`lossy_clone/chatbot.py`를 새로 만든다. `docs/ARCHITECTURE.md`의 "데이터 흐름 (1단계)"를 그대로 구현한다:

```
사용자 입력 → Chatbot.chat(message) → STM에 사용자 메시지 저장
→ STM에서 최근 대화 이력 읽기 → 응답 생성 (LLM 호출) → STM에 응답 저장 → 응답 반환
```

### 인터페이스

```python
from pathlib import Path
from lossy_clone.llm import LLMClient, GeminiLLMClient

class Chatbot:
    def __init__(
        self,
        llm_client: LLMClient | None = None,
        db_path: str | Path | None = None,
        session_id: str | None = None,
        history_limit: int = 20,
    ):
        """llm_client가 없으면 GeminiLLMClient()를 기본으로 사용한다.
        db_path가 없으면 lossy_clone 패키지 위치 기준 data/chatbot.db를 사용한다
        (Path(__file__) 기준 계산 — 현재 작업 디렉토리에 의존하지 말 것).
        session_id가 없으면 새 세션 UUID를 생성한다."""
        ...

    def chat(self, message: str) -> str:
        """사용자 메시지를 STM에 저장하고, 최근 history_limit개 메시지를 읽어
        LLM을 호출한 뒤, 응답을 STM에 저장하고 반환한다."""
        ...
```

### 핵심 규칙 (반드시 지켜야 함)

- `db_path` 기본값은 반드시 `Path(__file__).resolve().parent / "data" / "chatbot.db"` 방식으로 **패키지 위치 기준**으로 계산한다. `os.getcwd()`나 상대경로 문자열(`"data/chatbot.db"`)을 그대로 쓰지 마라 — `pytest`가 저장소 루트에서 실행되므로 CWD에 의존하면 `lossy_clone/` 폴더를 다른 위치로 옮겼을 때 깨진다 (ADR-001 독립성).
- `db_path`의 부모 디렉토리가 없으면 생성한다 (`Path.mkdir(parents=True, exist_ok=True)`).
- 매 `chat()` 호출 시 DB 커넥션을 새로 열고 `init_stm()`을 호출해 테이블이 없으면 만들도록 하거나, 생성자에서 한 번 커넥션을 열고 재사용해도 된다 — 어느 쪽이든 여러 번 연속 `chat()` 호출이 동일 세션의 대화 맥락을 정확히 누적해야 한다.
- `chat()`이 LLM에 전달하는 `messages`에는 방금 저장한 사용자 메시지를 포함한 최근 대화 이력이 순서대로 들어가야 한다 (STM에서 다시 읽어온 것을 사용 — 로컬 변수로 들고 있던 값을 그대로 쓰지 말 것. 이유: STM에 실제로 왕복 저장/조회되는지가 이 step의 핵심 검증 대상이다).
- LLM 응답을 받은 뒤 `role="assistant"`로 STM에 저장한다.
- `lossy_clone/README.md`의 "실행 방법" 섹션을 실제 동작하는 사용 예시로 교체한다 (예: `from lossy_clone.chatbot import Chatbot` 후 `bot = Chatbot(); bot.chat("...")` 형태 — `GEMINI_API_KEY` 환경변수가 필요하다는 점도 명시).

### 테스트

`lossy_clone/tests/test_chatbot.py`를 먼저 작성하고 통과하는 구현을 만들어라 (TDD). 실제 네트워크 호출 없이 `FakeLLMClient`를 주입해서 테스트한다. `db_path`는 `tmp_path` fixture로 임시 파일을 사용한다.

- `Chatbot(llm_client=FakeLLMClient(...), db_path=tmp_path / "test.db")`를 만들고 `chat("hello")`를 호출하면 `FakeLLMClient`가 반환하는 값이 그대로 반환되는지.
- 같은 `Chatbot` 인스턴스로 `chat()`을 두 번 이상 연속 호출했을 때, 두 번째 호출에서 `FakeLLMClient.generate()`에 전달된 `messages`에 첫 번째 턴의 user/assistant 메시지가 모두 포함되는지 (STM 왕복 검증 — `FakeLLMClient`의 `received_calls` 기록을 사용).
- `chat()` 호출 후 DB 파일에 실제로 user/assistant 메시지 row가 쌓이는지 (직접 `sqlite3.connect(db_path)`로 열어 `stm_messages` 테이블을 조회해 확인).
- `db_path`를 지정하지 않고 기본값으로 `Chatbot`을 만들었을 때, 계산된 경로가 CWD가 아니라 `lossy_clone` 패키지 위치 기준인지 확인하는 테스트도 추가한다 (예: 다른 CWD에서 실행해도 항상 같은 절대경로가 나오는지).

## Acceptance Criteria

```bash
python -m compileall -q .   # 구문 오류 없음
python -m pytest            # 테스트 통과
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `ARCHITECTURE.md`의 "데이터 흐름 (1단계)"를 정확히 구현했는가?
   - `ADR.md`(ADR-001 독립성/경로, ADR-003 1단계 범위, ADR-004 실제 동작하는 LLM 연동)를 벗어나지 않았는가?
   - `CLAUDE.md` CRITICAL 규칙(`lossy_clone/` 밖 파일 미참조, 벤더 SDK 비의존)을 위반하지 않았는가?
3. 결과에 따라 `phases/1-chatbot-stm/index.json`의 `step 5` 항목을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary"`에 생성한 파일과 `Chatbot` 사용법을 한 줄로 요약
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- LTM 전이, Episodic 누적, topic tagging, 반복 질문 감지 등 2/3/4단계 기능을 앞당겨 구현하지 마라 (ADR-003). 이 step은 오직 `Chatbot` + STM 왕복만 다룬다.
- `db_path` 기본값을 CWD 상대경로로 계산하지 마라. 이유: 위 핵심 규칙 참고 — 폴더 이식성이 깨진다.
- `GeminiLLMClient`를 테스트에서 기본으로 사용하지 마라. 테스트는 반드시 `FakeLLMClient`를 주입해서 네트워크 없이 실행되어야 한다.
- 저장소 루트의 `chatbot.py`(원본)를 import하거나 그대로 복사하지 마라. 개념(흐름)만 참고하고 코드는 새로 작성한다.
- `import chatbot`, `import memory`처럼 접두사 없는 절대 import를 쓰지 마라. 반드시 `lossy_clone.` 접두사 또는 패키지 내부 상대 import를 사용하라.
- 기존 테스트를 깨뜨리지 마라.
