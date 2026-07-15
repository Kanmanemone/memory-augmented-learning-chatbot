# Step 0: episodic-schema

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/PRD.md` (특히 "3단계: Episodic memory" 항목)
- `/docs/ARCHITECTURE.md`
- `/docs/ADR.md` (ADR-005, ADR-006 — 이번 step에서 추가할 ADR-007과 같은 결을 따른다)
- `lossy_clone/memory/ltm.py` — 이번 step에서 그대로 따라갈 패턴(DDL, `init_x`/저장/조회 함수 구조, DB 경로를 모듈이 하드코딩하지 않는 원칙, id/timestamp 생성 방식)
- `lossy_clone/tests/test_ltm.py` — 테스트 작성 패턴 참고
- `lossy_clone/chatbot.py` — 다음 step(`chatbot-episodic-extraction`)에서 이 모듈을 어떻게 사용할지 미리 파악해두면 인터페이스 설계에 도움이 된다 (지금 단계에서 `chatbot.py`를 수정하지는 않는다)

## 작업

### 1. `docs/ADR.md`에 ADR-007 추가

기존 ADR과 같은 문체(결정/이유/트레이드오프)로 아래 결정을 기록한다:

- **결정**: Episodic은 `topic`/`strengths`/`weaknesses`/`questions`만 담는 append-only 테이블로 시작한다. 원본의 taxonomy(9개 카테고리 enum), confidence score, 임베딩+Chroma, `SequenceMatcher` 기반 반복 감지(repeat-detection), source_message_ids/turn_indices/timestamps 추적, `occurrence_count` 같은 필드/로직은 넣지 않는다. 같은 `topic`이 여러 세션에 걸쳐 반복돼도 기존 레코드에 병합(upsert)하지 않고, 매번 새 row로 쌓는다.
- **이유**: `PRD.md` MVP 제외 사항에 "반복 질문 감지", "Chroma 등 벡터 DB, 임베딩 검색"이 명시적으로 4단계 스코프로 빠져 있다. LTM(`ltm` 테이블, ADR-005/006)도 같은 이유로 병합 없이 append-only로 설계했으므로 Episodic도 같은 패턴을 따른다.
- **트레이드오프**: 같은 주제를 여러 세션에서 반복 학습해도 "누적된 하나의 학습 이력"으로 합쳐 보여줄 수 없고, 세션별로 흩어진 row들을 나중에(4단계) 조회/집계해야 한다.

### 2. `lossy_clone/memory/episodic.py` 신규 작성

```python
import sqlite3

def init_episodic(conn: sqlite3.Connection) -> None:
    """episodic 테이블을 생성한다 (이미 있으면 아무 것도 하지 않음, 멱등)."""
    ...

def save_episodes(conn: sqlite3.Connection, session_id: str, episodes: list[dict]) -> list[dict]:
    """episodes의 각 항목(topic/strengths/weaknesses/questions 키를 가진 dict)마다
    새 row 하나씩 저장하고, 저장된 row들을 dict 리스트로 반환한다."""
    ...

def get_episodes_by_session(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """해당 session_id의 episodic row를 created_at 오름차순으로 전부 반환한다."""
    ...
```

`episodes`에 들어오는 각 dict의 형태: `{"topic": str, "strengths": list[str], "weaknesses": list[str], "questions": list[str]}`.

### 핵심 규칙 (반드시 지켜야 함)

- DB 경로 결정은 이 모듈의 책임이 아니다. `ltm.py`와 마찬가지로 호출자가 넘긴 `sqlite3.Connection`을 그대로 사용한다.
- 스키마는 정확히 `episodic(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, topic TEXT NOT NULL, strengths TEXT NOT NULL DEFAULT '[]', weaknesses TEXT NOT NULL DEFAULT '[]', questions TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL)`만 만든다. `topic_tags`/`confidence`/`source_*`/`occurrence_count`/`topic_embedding` 등을 추가하지 않는다 (ADR-007).
- `strengths`/`weaknesses`/`questions`는 SQLite에는 `json.dumps(..., ensure_ascii=False)`로 직렬화한 TEXT로 저장하고, 조회 시(`get_episodes_by_session`) `json.loads`로 역직렬화해 파이썬 `list`로 반환한다 (`ltm.py`에는 없던 부분이므로 새로 작성).
- `id`는 `uuid4()` 문자열, `created_at`은 `datetime.now(timezone.utc).isoformat()` — `ltm.py`의 `save_summary`와 동일한 패턴을 따른다.
- `session_id`에 대한 인덱스(`idx_episodic_session`)를 `ltm.py`의 `idx_ltm_session`처럼 추가한다.
- `save_episodes`는 입력값을 그대로 신뢰하고 저장한다 — 빈 `topic` 필터링이나 LLM 응답 검증 같은 로직은 이 모듈의 책임이 아니다 (그건 다음 step에서 `chatbot.py`가 LLM 응답을 파싱할 때 처리한다). 이 모듈은 `ltm.py`처럼 순수 저장/조회 계층으로만 남긴다.

### 테스트 (TDD — 먼저 작성하고 통과하는 구현을 만들 것)

`lossy_clone/tests/test_episodic.py`를 새로 작성한다. `test_ltm.py`의 `conn` fixture 패턴을 그대로 따른다.

- `init_episodic`을 두 번 호출해도 에러 없이 테이블이 하나만 존재하는지 (멱등성).
- `save_episodes(conn, session_id="s1", episodes=[{...}, {...}])`처럼 2개 이상의 episode를 한 번에 저장하면, 반환된 리스트의 각 dict에 `id`/`session_id`/`topic`/`strengths`/`weaknesses`/`questions`/`created_at`이 모두 채워져 있는지, `strengths` 등이 `list` 타입으로 반환되는지 (문자열이 아님).
- 같은 `session_id`로 여러 번(혹은 한 번에 여러 개) 저장한 뒤 `get_episodes_by_session`이 `created_at` 오름차순으로 전부 반환하는지, 각 필드가 `list`로 정확히 역직렬화되는지.
- 서로 다른 `session_id`로 저장했을 때 `get_episodes_by_session`이 요청한 `session_id`의 것만 반환하는지 (다른 세션과 섞이지 않는지).
- `strengths`/`weaknesses`/`questions`가 빈 리스트인 episode도 정상 저장/조회되는지.

## Acceptance Criteria

```bash
python -m compileall -q .   # 구문 오류 없음
python -m pytest            # 테스트 통과
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `ADR.md`에 ADR-007이 실제로 추가되었는가?
   - `episodic.py`가 taxonomy/confidence/source tracking/occurrence_count/embedding 등 3단계 이후(사실상 4단계) 필드를 앞당겨 만들지 않았는가 (ADR-003, ADR-007)?
   - 같은 `topic`을 병합(upsert)하는 로직이 없는가 (ADR-007 — 매번 새 row)?
   - `CLAUDE.md` CRITICAL 규칙(`lossy_clone/` 밖 파일 미참조, 벤더 SDK 비의존)을 위반하지 않았는가?
3. 결과에 따라 `phases/3-episodic/index.json`의 `step 0` 항목을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary"`에 생성한 파일과 `episodic.py`의 함수 시그니처를 한 줄로 요약
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- taxonomy enum, confidence score, source_message_ids/turn_indices/timestamps, occurrence_count, topic_embedding, Chroma 연동을 넣지 마라. 이유: ADR-007, PRD 4단계 스코프를 앞당기는 것이다.
- 같은 `topic`이면 기존 row에 병합(upsert)하는 로직을 넣지 마라 (ADR-007 — LTM과 동일하게 append-only).
- 저장소 루트의 `episodic_schema.py`(원본)를 import하거나 그대로 복사하지 마라. 개념(주제별 강점/약점/질문 저장)만 참고하고 코드는 새로 작성한다.
- 이번 step에서 `lossy_clone/chatbot.py`를 수정하지 마라 — 그건 다음 step(`chatbot-episodic-extraction`)의 몫이다.
- `save_episodes` 안에서 빈 `topic`을 걸러내거나 LLM 응답을 검증하는 로직을 넣지 마라 — 그건 다음 step의 몫이다.
- `import chatbot`, `import memory`처럼 접두사 없는 절대 import를 쓰지 마라. 반드시 `lossy_clone.` 접두사 또는 패키지 내부 상대 import를 사용하라.
- 기존 테스트를 깨뜨리지 마라.
