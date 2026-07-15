# Step 0: retrieval-functions

## 읽어야 할 파일

- `/docs/PRD.md` (특히 "4단계: 검색/통합 및 디테일" 항목)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md` (ADR-005/007 — Chroma/임베딩 배제 원칙)
- `lossy_clone/memory/ltm.py` — 현재 `init_ltm`/`save_summary`/`get_summaries_by_session`
- `lossy_clone/memory/episodic.py` — 현재 `init_episodic`/`save_episodes`/`get_episodes_by_session`
- `lossy_clone/tests/test_ltm.py`, `lossy_clone/tests/test_episodic.py` — 테스트 패턴

## 작업

### 1. `docs/ADR.md`에 ADR-010 추가

- **결정**: `search_ltm`/`search_episodic`는 쿼리 텍스트와 저장된 내용(LTM `summary`, Episodic `topic`/`strengths`/`weaknesses`/`questions`)을 단순 소문자 토큰 집합 오버랩으로 스코어링해 랭킹한다. Chroma나 임베딩은 쓰지 않는다.
- **이유**: PRD MVP 제외사항의 "Chroma 등 벡터 DB, 임베딩 검색"이 계속 배제 대상이고(ADR-005/007), 원본의 벡터+SQLite 하이브리드 스코어링은 PRD 4단계("필요한 것만 선택적으로")에 비해 과하다.
- **트레이드오프**: 의미적으로 비슷해도 단어가 안 겹치면 못 찾는다 (예: "지우개"와 "eraser"는 매칭 안 됨). 원본 수준의 검색 품질은 기대하지 않는다.

### 2. `lossy_clone/memory/ltm.py`에 `search_ltm` 추가

```python
def search_ltm(conn: sqlite3.Connection, query: str, limit: int = 3) -> list[dict]:
    """query와 토큰이 겹치는 summary를 가진 row를 겹침 개수 내림차순(동점이면 최신 우선)으로
    최대 limit개 반환한다. 겹치는 게 하나도 없으면 빈 리스트."""
    ...
```

### 3. `lossy_clone/memory/episodic.py`에 `search_episodic` 추가

```python
def search_episodic(conn: sqlite3.Connection, query: str, limit: int = 3) -> list[dict]:
    """query와 topic/strengths/weaknesses/questions를 합친 텍스트의 토큰이 겹치는 row를
    겹침 개수 내림차순(동점이면 최신 우선)으로 최대 limit개 반환한다."""
    ...
```

## 핵심 규칙 (반드시 지켜야 함)

- 두 함수 모두 시작할 때 `init_ltm(conn)`/`init_episodic(conn)`을 먼저 호출한다 — `chat()`이 `end_session()`을 한 번도 안 거친 새 DB에서도 호출될 수 있으므로, 테이블이 없을 때 "no such table" 에러가 나면 안 된다.
- 토큰화는 단순하게: `query.lower().split()`처럼 공백 기준 소문자 분리로 충분하다 (형태소 분석 등 넣지 마라 — 디테일은 의도적으로 뭉갠다, ADR-002).
- 점수 계산은 "쿼리 토큰 집합과 대상 텍스트 토큰 집합의 교집합 크기" 정도의 단순한 정수 점수면 된다. 정교한 정규화나 가중치는 넣지 마라 (ADR-010).
- `session_id`로 검색 범위를 제한하지 마라 — Episodic/LTM은 세션을 넘나드는 기억이 목적이므로, 전체 테이블에서 검색해야 한다.
- 반환하는 dict의 필드 구성은 각각 `get_summaries_by_session`/`get_episodes_by_session`이 반환하는 것과 동일해야 한다 (Episodic의 `strengths`/`weaknesses`/`questions`는 `list`로 역직렬화된 상태).
- 겹침 점수가 0인 row는 결과에 포함하지 마라 (완전히 무관한 내용을 억지로 끼워넣지 않는다).

## 테스트 (TDD — 먼저 작성하고 통과하는 구현을 만들 것)

`lossy_clone/tests/test_ltm.py`에 추가:
- 토큰이 겹치는 summary를 가진 row가 검색되는지, 안 겹치면 빈 리스트인지.
- 여러 row 중 겹침이 더 많은 row가 먼저 오는지 (랭킹 검증).
- `limit`을 넘는 결과가 있을 때 상위 `limit`개만 반환하는지.
- `init_ltm`을 미리 호출하지 않은 `:memory:` 커넥션에서 바로 `search_ltm`을 호출해도 에러 없이 동작하는지 (테이블 자동 생성 검증).

`lossy_clone/tests/test_episodic.py`에 추가:
- `topic`/`strengths`/`weaknesses`/`questions` 각각에 겹치는 단어가 있을 때 전부 매칭되는지 (네 필드 중 하나만 겹쳐도 찾아지는지).
- 랭킹, `limit`, 빈 결과, 미초기화 커넥션 케이스도 `test_ltm.py`와 동일하게 커버.

## Acceptance Criteria

```bash
python -m compileall -q .
python -m pytest
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. `ADR.md`에 ADR-010이 추가되었는지, Chroma/임베딩을 쓰지 않았는지, `session_id`로 검색 범위를 제한하지 않았는지 확인한다.
3. `phases/4-retrieval/index.json`의 `step 0`을 업데이트한다.

## 금지사항

- Chroma, 임베딩, 벡터 유사도 계산을 넣지 마라 (ADR-010).
- 검색을 `session_id`로 제한하지 마라.
- 이번 step에서 `lossy_clone/chatbot.py`를 수정하지 마라 — `chat()` 연결은 이후 step의 몫이다.
- `import chatbot`, `import memory`처럼 접두사 없는 절대 import를 쓰지 마라.
- 기존 테스트를 깨뜨리지 마라.
