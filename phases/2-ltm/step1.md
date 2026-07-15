# Step 1: stm-full-history

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/PRD.md` (특히 "2단계: LTM" 항목)
- `/docs/ARCHITECTURE.md`
- `/docs/ADR.md` (ADR-005 포함)
- `lossy_clone/memory/stm.py` — 현재 `get_recent_messages(conn, session_id, limit: int = 20)` 구현
- `lossy_clone/tests/test_stm.py` — 기존 테스트 패턴
- `lossy_clone/chatbot.py` — `Chatbot.chat()`이 `get_recent_messages`를 정수 `limit`으로 호출하는 지점 (이번 step에서 수정하지 않지만, 회귀가 없는지 확인하는 기준이 된다)

이후 step(`chatbot-end-session`, step 2)에서 STM 전체 이력을 요약 대상으로 읽을 때 이 함수를 사용한다. 지금 단계에서는 `chatbot.py`나 `end_session` 관련 코드를 만들지 않는다.

## 작업

`lossy_clone/memory/stm.py`의 `get_recent_messages` 하나만 확장한다.

- `limit` 파라미터를 `Optional[int] = 20`으로 바꾼다.
- `limit=None`이면 `LIMIT` 절 없이 해당 `session_id`의 전체 메시지를 `turn_index` 오름차순으로 반환한다.
- `limit`이 정수로 주어지면 기존 동작(최근 N개를 `turn_index DESC LIMIT`으로 골라 `reversed()`로 오름차순 재정렬)을 그대로 유지한다.

## 핵심 규칙 (반드시 지켜야 함)

- 기존 호출부(`Chatbot.chat()`)는 정수 `limit`을 그대로 넘기므로 동작이 바뀌면 안 된다 — 시그니처 확장이지 동작 변경이 아니다.
- SQL은 `limit is None`일 때와 정수일 때를 분기해서 처리한다 (`LIMIT -1` 같은 SQLite 트릭에 기대지 말고, 쿼리 문자열 자체를 분기할 것 — 명시성이 우선이다).
- 이번 step에서 `lossy_clone/chatbot.py`를 수정하지 마라 — `end_session`은 step 2의 몫이다.
- `lossy_clone/memory/ltm.py`를 참조하거나 수정하지 마라 — 이번 step은 STM만 다룬다.

## 테스트 (TDD — 먼저 작성하고 통과하는 구현을 만들 것)

`lossy_clone/tests/test_stm.py`에 추가:

- `limit=None`으로 호출하면 5개 이상 메시지를 저장한 세션에서도 전부, `turn_index` 오름차순으로 반환되는지.
- 기존 `limit=2` 등 정수 지정 테스트가 여전히 통과하는지 (회귀 확인 — 기존 테스트를 고치는 게 아니라 새 케이스를 추가해 커버한다).

## Acceptance Criteria

```bash
python -m compileall -q .   # 구문 오류 없음
python -m pytest            # 테스트 통과
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 기존 `test_stm.py`의 정수 `limit` 테스트가 전부 그대로 통과하는가 (회귀 없음)?
   - `lossy_clone/chatbot.py`를 건드리지 않았는가?
3. 결과에 따라 `phases/2-ltm/index.json`의 `step 1` 항목을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary"`에 변경한 함수 시그니처를 한 줄로 요약
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `lossy_clone/chatbot.py`에 `end_session`을 만들지 마라 (step 2 몫).
- `lossy_clone/memory/ltm.py`를 만들거나 참조하지 마라 (이미 step 0에서 완료된 범위).
- `import memory`처럼 접두사 없는 절대 import를 쓰지 마라.
- 기존 테스트를 깨뜨리지 마라.
