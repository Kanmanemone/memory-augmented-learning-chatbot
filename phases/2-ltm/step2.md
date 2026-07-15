# Step 2: chatbot-end-session

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/PRD.md` (특히 "2단계: LTM" 항목)
- `/docs/ARCHITECTURE.md`
- `/docs/ADR.md` (ADR-005, 이번 step에서 추가할 ADR-006)
- `lossy_clone/memory/stm.py` — step 1에서 확장된 `get_recent_messages(conn, session_id, limit=None)`
- `lossy_clone/memory/ltm.py` — step 0에서 만든 `init_ltm`/`save_summary`/`get_summaries_by_session`
- `lossy_clone/chatbot.py` — 현재 `Chatbot.__init__`/`chat()` 구현 (커넥션을 매 호출마다 열고 닫는 패턴)
- `lossy_clone/llm.py` — `LLMClient`/`FakeLLMClient`의 `generate(messages)` 시그니처, `messages`의 `role`이 `"system"`도 지원한다는 점 (`GeminiLLMClient._build_payload` 참고)
- `lossy_clone/tests/test_chatbot.py` — 테스트 작성 패턴

이전 step에서 만들어진 `ltm.py`/`stm.py`의 실제 함수 시그니처를 코드에서 직접 확인한 뒤 작업하라 (이 문서에 적힌 시그니처와 실제 구현이 미묘하게 다를 수 있으므로 실제 코드를 신뢰하라).

## 작업

### 1. `docs/ADR.md`에 ADR-006 추가

기존 ADR-001~005와 같은 문체(결정/이유/트레이드오프)로 아래 결정을 기록한다:

- **결정**: 세션 종료는 자동 감지(exit 키워드 파싱, 최대 턴 수 도달, 유휴 시간 초과 등)가 아니라 `Chatbot.end_session()` 명시적 호출로만 트리거한다. 같은 `Chatbot` 인스턴스에서 `end_session()`을 여러 번 호출하면 그때마다 STM 전체 이력이 다시 요약되어 LTM에 별도 row로 중복 저장될 수 있다 — 이를 막는 상태 추적(예: "이미 종료된 세션" 플래그)은 만들지 않는다.
- **이유**: 원본의 `session_manager.py`처럼 exit-command 감지·`max_turns`·`inactivity_timeout` 판단 로직을 갖추는 것은 `PRD.md` 4단계("반복 질문 감지 등 세부 동작") 스코프다. 2단계는 "요약해서 LTM에 넘긴다"는 흐름 자체가 동작하는 것이 목표다.
- **트레이드오프**: 호출자가 `end_session()`을 실수로 여러 번 부르면 중복 요약이 쌓인다. 지금은 감수하고, 필요해지면 이후 단계에서 다룬다.

### 2. `lossy_clone/chatbot.py`에 `end_session` 추가

```python
def end_session(self) -> Optional[str]:
    """현재 session_id의 STM 전체 이력을 요약해 LTM에 저장하고, 요약 텍스트를 반환한다.
    STM에 메시지가 하나도 없으면 아무 것도 하지 않고 None을 반환한다."""
    ...
```

## 핵심 규칙 (반드시 지켜야 함)

- `get_recent_messages(conn, session_id, limit=None)`으로 STM **전체** 이력을 읽는다 (step 1에서 이미 구현됨). `chat()`에서 쓰는 `self._history_limit`과는 무관하다 (요약 대상과 대화 컨텍스트 대상은 다른 개념이다).
- STM 이력이 비어 있으면 LLM을 호출하지 않고 `None`을 반환한다 (빈 대화를 요약시키지 않는다).
- LLM에 넘기는 `messages`는 STM에서 읽은 user/assistant 메시지를 **그대로만** 전달하면 안 된다 — 일반 `chat()` 호출과 구분되는 지시(예: `role="system"`으로 "이 대화를 요약하라"는 취지의 메시지 하나)를 반드시 포함시켜야 한다. 정확한 문구는 자유롭게 정하되, STM raw 메시지만 보내면 LLM이 요약해야 한다는 것을 알 방법이 없다는 점을 지켜라.
- 요약 지시용 `role="system"` 메시지는 LLM 호출에만 포함시키고, `add_message`로 STM에 저장하지 마라 (실제 대화 기록이 아니므로 `stm_messages`에 섞이면 안 된다).
- LLM 응답을 `lossy_clone.memory.ltm.save_summary(conn, session_id=self.session_id, summary=<응답>)`로 저장한다. `chat()`과 동일하게 커넥션을 열 때 `init_ltm(conn)`도 호출해 테이블이 없으면 만들도록 한다.
- `end_session()` 호출 여부를 추적하는 새 필드(`is_active`, `ended_at` 등)를 `Chatbot`에 추가하지 마라 — ADR-006에서 명시한 대로 중복 호출은 의도적으로 막지 않는다.

## 테스트

TDD로 먼저 작성하고 통과하는 구현을 만들어라. 실제 네트워크 호출 없이 `FakeLLMClient`를 주입한다.

`lossy_clone/tests/test_chatbot.py`에 추가:

- 새 `Chatbot`으로 `chat()`을 한 번도 호출하지 않고 바로 `end_session()`을 호출하면 `None`이 반환되고, LTM 테이블에 row가 생기지 않는지.
- `chat("hi")`, `chat("more")`를 호출한 뒤 `end_session()`을 호출하면, `FakeLLMClient.received_calls`의 **마지막** 호출에 두 턴의 user/assistant 메시지가 모두 포함되어 있고, 그 이전 `chat()` 호출들에는 없던 지시성 메시지(예: `role="system"`)가 추가로 포함되어 있는지.
- `end_session()`의 반환값이 실제로 DB 파일의 `ltm` 테이블에 저장되어 있는지 (`sqlite3.connect(db_path)`로 직접 열어 확인).
- 같은 `Chatbot` 인스턴스에서 `end_session()`을 두 번 연달아 호출하면, LTM 테이블에 해당 `session_id`의 row가 2개 쌓이는지 (ADR-006에서 의도적으로 감수하기로 한 중복 저장 동작을 실제로 검증한다).

## Acceptance Criteria

```bash
python -m compileall -q .   # 구문 오류 없음
python -m pytest            # 테스트 통과
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `ADR.md`에 ADR-006이 실제로 추가되었는가?
   - `end_session()`이 STM 전체 이력을 읽고, 요약 후 LTM에 저장하는 흐름을 정확히 구현했는가 (`ARCHITECTURE.md`에는 step 4에서 이 흐름을 반영할 것이다)?
   - exit-command 감지, `max_turns`, `inactivity_timeout` 같은 4단계 기능을 앞당겨 만들지 않았는가 (ADR-006)?
   - `struggles`/`strengths`/`confusions` 등을 요약과 별도로 추출해 저장하지 않았는가 (ADR-005)?
   - 요약 지시용 `role="system"` 메시지가 `stm_messages`에 저장되지 않고 LLM 호출에만 쓰였는가?
3. 결과에 따라 `phases/2-ltm/index.json`의 `step 2` 항목을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary"`에 변경한 파일과 `end_session()` 사용법을 한 줄로 요약
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- exit 키워드 감지, `max_turns`, `inactivity_timeout` 등 자동 세션 종료 판단 로직을 만들지 마라 (ADR-006, PRD 4단계 스코프).
- `struggles`/`strengths`/`confusions`/`topic_tags` 등을 별도로 LLM에 추출시켜 저장하지 마라 (ADR-005, PRD 3단계 스코프). 이번 step은 오직 하나의 요약 텍스트만 다룬다.
- `end_session()`의 중복 호출을 막는 상태 플래그나 세션 재시작 로직을 추가하지 마라 (ADR-006에서 의도적으로 감수하기로 한 부분).
- `GeminiLLMClient`를 테스트에서 기본으로 사용하지 마라. 테스트는 반드시 `FakeLLMClient`를 주입해서 네트워크 없이 실행되어야 한다.
- `lossy_clone/__main__.py`를 수정하지 마라 — CLI에서 `end_session()`을 실제로 호출하는 배선은 step 3의 몫이다.
- `docs/ARCHITECTURE.md`, `lossy_clone/README.md`를 갱신하지 마라 — 문서 동기화는 step 4의 몫이다.
- `import chatbot`, `import memory`처럼 접두사 없는 절대 import를 쓰지 마라.
- 기존 테스트를 깨뜨리지 마라.
