# Step 2: chat-integration

## 읽어야 할 파일

- `/docs/ADR.md` (ADR-009, ADR-010 — 이번 step에서 ADR-011을 추가한다)
- `lossy_clone/chatbot.py` — 현재 `Chatbot.chat()` 구현과 step 1에서 만든 `build_memory_context`
- `lossy_clone/memory/ltm.py`/`episodic.py`의 `search_ltm`/`search_episodic`
- `lossy_clone/tests/test_chatbot.py` — 기존 `chat()` 테스트 4개(`test_chat_returns_fake_llm_response`, `test_chat_passes_accumulated_history_to_llm_on_second_call`, `test_chat_persists_messages_to_stm`, `test_default_db_path_is_package_relative_not_cwd`) — 이번 step에서 이 테스트들이 회귀 없이 통과해야 한다 (전부 빈 DB에서 시작하므로 검색 결과가 없어 컨텍스트가 안 붙는 경로를 타야 정상이다).

## 작업

### 1. `docs/ADR.md`에 ADR-011 추가

- **결정**: `chat()`은 매 턴 `search_ltm`+`search_episodic`을 호출해 결과가 있으면 `build_memory_context()`로 만든 텍스트 하나를 `role="system"`으로 STM 이력 **앞**에 붙인다. 원본처럼 반복 질문 여부를 별도 임계값/사후검증으로 판정하지 않고, 검색된 과거 Episodic 질문을 컨텍스트에 포함시켜 "관련 있으면 참고해서 답하라"는 지시만 LLM에 준다.
- **이유**: `chat()`의 STM 이력은 항상 사용자가 방금 저장한 메시지로 끝나므로, system 메시지를 이력 앞에 붙여도 Gemini에 보내는 마지막 turn은 그대로 `user`로 유지된다 (`end_session()`과 반대 상황 — ADR-009 위반 아님). 반복 질문 판정 로직은 원본에서도 복잡한 부가 기능이라 PRD "필요한 것만 선택적으로"에 맞춰 생략한다.
- **트레이드오프**: 검색에 걸리는 내용이 없으면(새 사용자, 무관한 질문) 컨텍스트 없이 기존과 동일하게 동작한다. 반복 질문을 실제로 언급하는지는 LLM 재량이라 보장되지 않는다.

### 2. `Chatbot.chat()` 확장

`add_message(user)` 이후, `get_recent_messages` 이전에 검색을 끼워넣는다:

```
... (기존: init_stm, add_message(user))
→ search_ltm(conn, query=message, limit=3), search_episodic(conn, query=message, limit=3)
→ build_memory_context(ltm_hits, episodic_hits)
→ get_recent_messages (기존과 동일)
→ llm_messages 구성: 컨텍스트가 있으면 [{"role": "system", "content": 컨텍스트}] + 기존 llm_messages, 없으면 기존 llm_messages 그대로
→ generate(llm_messages)
... (기존: add_message(assistant), return reply)
```

## 핵심 규칙 (반드시 지켜야 함)

- 검색 쿼리는 이번 턴에 사용자가 방금 입력한 `message` 그 자체를 쓴다 (누적 STM 이력이 아니다).
- `build_memory_context`가 `None`을 반환하면 `llm_messages`에 아무것도 추가하지 않는다 — 기존 `chat()` 동작(컨텍스트 없이 STM 이력만 전달)과 완전히 동일해야 한다. 이 경로가 깨지면 기존 4개 테스트가 실패한다.
- 컨텍스트 `system` 메시지는 STM 이력보다 **앞**에 온다. STM에서 읽은 마지막 메시지(방금 저장한 사용자 메시지)가 여전히 `llm_messages`의 마지막이어야 한다 (ADR-009와 동일한 이유로 마지막 turn은 `user`여야 함 — 여기서는 이미 그렇게 되어 있으니 순서를 바꾸지 않도록 주의).
- 컨텍스트 `system` 메시지를 `add_message()`로 STM에 저장하지 마라 — `end_session()`의 지시 메시지와 동일한 이유(실제 대화 기록이 아니다).
- `Chatbot`에 새 필드를 추가하지 마라.

## 테스트

TDD로 먼저 작성하고 통과하는 구현을 만들어라. 기존 4개 `chat()` 테스트를 먼저 그대로 돌려서(수정 없이) 통과하는지 확인한 뒤, 아래 새 테스트를 추가한다.

`lossy_clone/tests/test_chatbot.py`에 추가:

- **LTM 컨텍스트 주입**: `Chatbot`으로 `end_session()`을 먼저 호출해(또는 `lossy_clone.memory.ltm.save_summary`를 직접 호출해) `ltm` 테이블에 특정 키워드가 담긴 summary를 만들어두고, 새 `Chatbot`(다른 session_id 가능)으로 그 키워드가 겹치는 메시지를 `chat()`하면, `FakeLLMClient.received_calls`의 마지막 호출 `messages[0]`이 `role="system"`이고 그 summary 텍스트를 포함하는지.
- **Episodic 컨텍스트 주입**: 위와 동일한 방식으로 `episodic` 테이블에 topic/questions를 미리 저장해두고, 겹치는 메시지로 `chat()`했을 때 시스템 컨텍스트에 포함되는지.
- **무관하면 컨텍스트 없음**: 저장된 내용과 전혀 겹치지 않는 메시지로 `chat()`하면 `messages[0]`이 `role="system"`이 아니라 그대로 `role="user"`(방금 입력한 메시지)인지 — 컨텍스트가 없을 때 기존 동작과 동일함을 직접 검증.
- **STM에 컨텍스트가 안 새는지**: 컨텍스트가 주입된 턴 이후에도 `stm_messages` 테이블에는 user/assistant 두 row만 쌓이는지 (`sqlite3.connect`로 직접 조회).
- **`messages`의 마지막은 항상 user**: 컨텍스트가 주입된 경우에도 `received_calls`의 마지막 호출에서 `messages[-1]["role"] == "user"`이고 `messages[-1]["content"]`가 이번 턴 사용자 입력인지.

## Acceptance Criteria

```bash
python -m compileall -q .
python -m pytest
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. `ADR.md`에 ADR-011이 추가되었는지 확인한다.
3. 기존 `chat()` 테스트 4개가 수정 없이 통과하는지 확인한다 (회귀 없음의 직접적 증거).
4. 컨텍스트 `system` 메시지가 STM에 저장되지 않는지, 검색 쿼리가 세션에 국한되지 않는지(step 0에서 이미 보장됨) 확인한다.
5. `phases/4-retrieval/index.json`의 `step 2`를 업데이트한다.

## 금지사항

- 반복 질문 감지를 위한 임계값/스코어링/사후검증 로직을 추가하지 마라 (ADR-011).
- 검색을 `session_id`로 제한하지 마라.
- 컨텍스트가 없을 때(빈 검색 결과) `llm_messages` 구성이나 기존 4개 테스트의 동작을 바꾸지 마라.
- `lossy_clone/__main__.py`를 수정하지 마라 — 이번 phase에서는 CLI 출력에 검색 결과를 노출하지 않는다.
- `GeminiLLMClient`를 테스트에서 기본으로 사용하지 마라.
- 기존 테스트를 깨뜨리지 마라.
