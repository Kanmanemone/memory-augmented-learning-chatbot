# Step 0: ltm-schema

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/PRD.md` (특히 "2단계: LTM" 항목)
- `/docs/ARCHITECTURE.md`
- `/docs/ADR.md`
- `lossy_clone/memory/stm.py` — 이번 step에서 그대로 따라갈 패턴(DDL, `init_x`/저장/조회 함수 구조, DB 경로를 모듈이 하드코딩하지 않는 원칙)
- `lossy_clone/tests/test_stm.py` — 테스트 작성 패턴 참고
- `lossy_clone/chatbot.py` — 이후 step(`chatbot-end-session`, step 2)에서 이 모듈을 어떻게 사용할지 미리 파악해두면 인터페이스를 설계하는 데 도움이 된다 (지금 단계에서 `chatbot.py`를 수정하지는 않는다)

## 작업

### 1. `docs/ADR.md`에 ADR-005 추가

기존 ADR-001~004와 같은 문체(결정/이유/트레이드오프)로 아래 결정을 기록한다:

- **결정**: LTM은 `summary`(세션 요약 텍스트) 하나만 담는 최소 스키마로 시작한다. 원본의 `struggles`/`strengths`/`confusions`/`topic_tags`/`embedding` 같은 필드는 지금 넣지 않는다.
- **이유**: `PRD.md` 로드맵상 강점/약점/혼란 누적은 3단계(Episodic memory), 임베딩/검색은 4단계 스코프다. 지금 한 번에 넣으면 ADR-003("단계적으로 쌓는다")을 어기게 된다.
- **트레이드오프**: 2단계가 끝난 시점에는 세션이 "무엇에 대한 대화였는지"만 남고, "무엇을 어려워했는지" 같은 학습 분석은 3단계 전까지 존재하지 않는다.

### 2. `lossy_clone/memory/ltm.py` 신규 작성

```python
import sqlite3

def init_ltm(conn: sqlite3.Connection) -> None:
    """ltm 테이블을 생성한다 (이미 있으면 아무 것도 하지 않음, 멱등)."""
    ...

def save_summary(conn: sqlite3.Connection, session_id: str, summary: str) -> dict:
    """세션 요약 한 건을 저장하고 저장된 row를 dict로 반환한다."""
    ...

def get_summaries_by_session(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """해당 session_id의 요약을 created_at 오름차순으로 전부 반환한다."""
    ...
```

### 핵심 규칙 (반드시 지켜야 함)

- DB 경로 결정은 이 모듈의 책임이 아니다. `stm.py`와 마찬가지로 호출자가 넘긴 `sqlite3.Connection`을 그대로 사용한다.
- 스키마는 정확히 `ltm(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL)`만 만든다. `struggles`/`strengths`/`confusions`/`topic_tags`/`embedding` 컬럼이나 Chroma 등 벡터 DB 연동을 추가하지 않는다 (ADR-005).
- `id`는 `uuid4()` 문자열, `created_at`은 `datetime.now(timezone.utc).isoformat()` — `stm.py`의 `add_message`와 동일한 패턴을 따른다.
- `session_id`에 대한 인덱스(`idx_ltm_session`)를 `stm.py`의 `idx_stm_session`처럼 추가한다.

### 테스트 (TDD — 먼저 작성하고 통과하는 구현을 만들 것)

`lossy_clone/tests/test_ltm.py`를 새로 작성한다. `test_stm.py`의 `conn` fixture 패턴(`sqlite3.connect(":memory:")` + `init_ltm`)을 그대로 따른다.

- `init_ltm`을 두 번 호출해도 에러 없이 테이블이 하나만 존재하는지 (멱등성).
- `save_summary`가 반환하는 dict에 `id`/`session_id`/`summary`/`created_at`이 모두 채워져 있는지.
- 같은 `session_id`로 여러 번 저장한 뒤 `get_summaries_by_session`이 `created_at` 오름차순으로 전부 반환하는지.
- 서로 다른 `session_id`로 저장했을 때 `get_summaries_by_session`이 요청한 `session_id`의 것만 반환하는지 (다른 세션과 섞이지 않는지).

## Acceptance Criteria

```bash
python -m compileall -q .   # 구문 오류 없음
python -m pytest            # 테스트 통과
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `ADR.md`에 ADR-005가 실제로 추가되었는가?
   - `ltm.py`가 `struggles`/`strengths`/`confusions`/`topic_tags`/`embedding` 등 3/4단계 필드를 앞당겨 만들지 않았는가 (ADR-003, ADR-005)?
   - `CLAUDE.md` CRITICAL 규칙(`lossy_clone/` 밖 파일 미참조, 벤더 SDK 비의존)을 위반하지 않았는가?
3. 결과에 따라 `phases/2-ltm/index.json`의 `step 0` 항목을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary"`에 생성한 파일과 `ltm.py`의 함수 시그니처를 한 줄로 요약
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `struggles`/`strengths`/`confusions`/`topic_tags`/`embedding` 컬럼이나 Chroma 등 벡터 DB 연동을 넣지 마라. 이유: ADR-005, PRD 3/4단계 스코프를 앞당기는 것이다.
- 저장소 루트의 `memory/ltm.py`(원본)를 import하거나 그대로 복사하지 마라. 개념(세션별 요약 저장)만 참고하고 코드는 새로 작성한다.
- 이번 step에서 `lossy_clone/chatbot.py`나 `lossy_clone/memory/stm.py`를 수정하지 마라 — 그건 다음 step(`chatbot-end-session`)의 몫이다.
- `import chatbot`, `import memory`처럼 접두사 없는 절대 import를 쓰지 마라. 반드시 `lossy_clone.` 접두사 또는 패키지 내부 상대 import를 사용하라.
- 기존 테스트를 깨뜨리지 마라.
