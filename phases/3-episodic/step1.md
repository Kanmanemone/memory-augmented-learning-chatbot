# Step 1: chatbot-episodic-extraction

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/PRD.md` (특히 "3단계: Episodic memory" 항목)
- `/docs/ARCHITECTURE.md`
- `/docs/ADR.md` (ADR-005, ADR-006, ADR-007 — ADR-008을 이번 step에서 추가한다)
- `lossy_clone/memory/episodic.py` — step 0에서 만든 `init_episodic`/`save_episodes`/`get_episodes_by_session`
- `lossy_clone/chatbot.py` — 현재 `end_session()` 구현: STM 전체 이력을 읽고, `_SUMMARY_INSTRUCTION` system 메시지를 붙여 `generate()`를 호출하고, 응답을 `memory.ltm.save_summary`로 저장하는 흐름을 그대로 이해한 뒤, 같은 패턴으로 두 번째 호출을 추가한다.
- `lossy_clone/llm.py` — `LLMClient`/`FakeLLMClient`의 `generate(messages)` 시그니처
- `lossy_clone/tests/test_chatbot.py` — 기존 `end_session()` 테스트 패턴 (특히 `role="system"` 지시 메시지로 일반 `chat()` 호출과 구분하는 방식)

이전 step에서 만들어진 `episodic.py`의 실제 함수 시그니처를 코드에서 직접 확인한 뒤 작업하라 (이 문서에 적힌 시그니처와 실제 구현이 미묘하게 다를 수 있으므로 실제 코드를 신뢰하라).

## 작업

### 1. `docs/ADR.md`에 ADR-008 추가

기존 ADR과 같은 문체로 아래 결정을 기록한다:

- **결정**: LTM 요약과 Episodic 추출(topic/strengths/weaknesses/questions)은 하나로 합치지 않고 별도의 `generate()` 호출로 수행한다. 두 호출은 순서가 고정되어 있다 — LTM 요약 호출이 먼저이고, 그 호출이 예외를 던지면 Episodic 추출은 시도하지 않고 예외를 그대로 전파한다. 반대로 Episodic 추출 호출이 실패(예외, 또는 파싱 불가능한 응답)해도 이미 계산된 LTM 요약의 저장과 반환에는 영향을 주지 않는다 — 조용히 건너뛴다.
- **이유**: 관심사 분리 — 요약은 자유 형식 텍스트, Episodic 추출은 구조화된 JSON이라 프롬프트 성격이 다르다. 실패 격리 — Episodic 추출(구조화 출력이라 LLM이 형식을 못 지킬 위험이 더 큼)이 흔들려도 이미 검증된 2단계 기능(LTM 요약)을 깨뜨리면 안 된다.
- **트레이드오프**: `end_session()` 한 번 호출에 LLM API 호출이 최대 2번 필요해 지연 시간과 비용이 늘어난다. 세션당 여러 주제를 허용하므로(리스트), 응답 계약은 `{"topics": [{"topic": str, "strengths": [str, ...], "weaknesses": [str, ...], "questions": [str, ...]}, ...]}` 형태이며 주제가 없으면 `{"topics": []}`를 기대한다.

### 2. `lossy_clone/chatbot.py`의 `end_session()` 확장

기존 흐름(STM 전체 조회 → 비어있으면 `None` → LTM 요약 호출 → 저장) 뒤에 아래 단계를 추가한다:

```
... (기존 LTM 요약 저장까지 끝난 뒤)
→ 별도 generate() 호출로 topic/strengths/weaknesses/questions 추출 (STM 이력 + episodic용 지시 메시지)
→ 응답을 파싱해 유효한 항목만 memory.episodic.save_episodes로 저장 (실패 시 조용히 건너뜀)
→ (기존과 동일) 요약 텍스트 반환
```

반환 타입/값은 바꾸지 않는다 — `end_session()`은 여전히 `Optional[str]`(LTM 요약 텍스트 또는 `None`)만 반환한다. Episodic 저장 결과(몇 개 저장됐는지 등)는 반환값에 포함하지 않는다.

## 핵심 규칙 (반드시 지켜야 함)

- Episodic 추출은 LTM 요약과 **완전히 별도의 `generate()` 호출**로 수행한다 (한 응답에 요약+topics를 함께 요청하지 않는다) — ADR-008.
- 이 두 번째 호출도 STM 전체 이력을 포함해야 한다 (주제를 뽑으려면 대화 내용이 필요하다). 메시지 구성은 LTM 요약 호출과 마찬가지로 `role="system"` 지시 메시지 + STM 이력이되, 지시 메시지 내용은 요약 지시와 달라야 한다 (테스트에서 두 호출을 구분할 수 있어야 한다).
- 이 호출은 반드시 LTM 요약 호출과 저장이 끝난 **뒤에** 시도한다. LTM 요약 호출(`generate()` 또는 `save_summary`)이 예외를 던지면 그 예외를 그대로 전파하고 Episodic 추출은 아예 시도하지 않는다.
- Episodic 추출 호출 자체가 예외를 던지거나, 응답이 JSON으로 파싱되지 않거나, `{"topics": [...]}` 형태가 아니면 `try/except`로 감싸 조용히 건너뛴다 (아무것도 저장하지 않고 `end_session()`은 정상적으로 요약을 반환한다) — ADR-008.
- 실제 Gemini는 "JSON으로 답하라"고만 지시해도 응답을 \`\`\`json ... \`\`\` 코드펜스로 감싸서 반환하는 경우가 흔하다(`llm.py`의 `GeminiLLMClient`는 `response_mime_type` 같은 JSON 강제 옵션을 쓰지 않으므로). 이 지시 메시지에는 "코드펜스나 설명 없이 순수 JSON 객체만 출력하라"는 문구를 포함하고, `json.loads()` 시도 전에 응답 문자열 앞뒤의 \`\`\`json / \`\`\` 코드펜스를 벗겨내는 전처리를 반드시 넣는다 — 이 전처리 없이는 `FakeLLMClient` 기반 테스트만 통과하고 실제 Gemini 응답에서는 매번 파싱에 실패해 조용히 아무것도 저장되지 않는 상태가 될 수 있다(ADR-004 위반 위험).
- 파싱된 `topics` 배열의 각 항목 중 `topic` 필드가 없거나 빈 문자열(공백만 있는 경우 포함)인 항목은 저장 대상에서 제외한다. `strengths`/`weaknesses`/`questions`가 없거나 리스트가 아니면 빈 리스트로 취급한다 (이 검증은 `chatbot.py`의 책임이다 — `episodic.py`는 입력을 그대로 신뢰한다).
- 이 두 번째 호출의 지시 메시지나 원본 JSON 응답을 `add_message()`로 STM에 저장하지 마라 (LTM 요약 지시와 동일한 이유 — 실제 대화 기록이 아니다).
- `Chatbot`에 새 필드(파싱 실패 이력 추적 등)를 추가하지 마라.

## 테스트

TDD로 먼저 작성하고 통과하는 구현을 만들어라. `FakeLLMClient`에 콜러블을 주입해 `messages` 내용(지시 메시지가 요약용인지 episodic용인지)에 따라 다른 응답을 반환하도록 구성한다 — 기존 `end_session()` 테스트가 `role="system"` 메시지로 일반 `chat()`과 요약 호출을 구분한 방식을 참고해, 이번엔 요약 지시와 episodic 지시를 서로 다른 문자열로 만들어 콜러블 안에서 분기하라.

`lossy_clone/tests/test_chatbot.py`에 추가:

- `chat()`을 두 번 이상 호출한 뒤 `end_session()`을 호출하면, episodic 추출용 응답(`{"topics": [{"topic": "decorators", "strengths": [...], ...}, {"topic": "recursion", ...}]}`)에 따라 `episodic` 테이블에 topic별로 row가 쌓이는지, 각 필드가 올바르게 저장됐는지 (`sqlite3.connect(db_path)`로 직접 조회).
- episodic 추출 응답이 코드펜스로 감싸져 온 경우(예: `` "```json\n{\"topics\": [{\"topic\": \"decorators\", ...}]}\n```" ``) → 코드펜스가 제거되고 정상 파싱되어 `episodic` 테이블에 저장되는지 (실제 Gemini 응답에서 흔한 형태를 검증하는 핵심 테스트).
- episodic 추출 응답이 `{"topics": []}`인 경우 → `episodic` 테이블에 row가 없지만, `ltm` 테이블엔 요약이 정상 저장되고 `end_session()` 반환값도 정상인지.
- episodic 추출 응답이 깨진 JSON(예: `"not json"`)인 경우 → 예외 없이 `end_session()`이 요약을 정상 반환하고 `ltm`에도 정상 저장되며, `episodic`엔 아무 row도 없는지.
- episodic 추출 응답의 `topics` 중 하나가 `topic: ""`(빈 문자열)인 경우 → 그 항목만 걸러지고 나머지 유효한 항목은 저장되는지.
- episodic 추출 호출 자체가 예외를 던지는 콜러블인 경우(요약 호출은 정상 응답) → `end_session()`이 예외 없이 요약을 정상 반환/저장하는지 (ADR-008의 실패 격리를 직접 검증하는 핵심 테스트).
- **LTM 요약 호출이 실패하는 경우** → `end_session()`이 예외를 전파하고, `FakeLLMClient.received_calls`에 episodic 추출 호출이 아예 없었는지(길이로 확인) — 순서 보장을 검증한다.

## Acceptance Criteria

```bash
python -m compileall -q .   # 구문 오류 없음
python -m pytest            # 테스트 통과
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `ADR.md`에 ADR-008이 실제로 추가되었는가?
   - LTM 요약과 Episodic 추출이 정확히 별도의 `generate()` 호출인가 (ADR-008)?
   - LTM 요약 실패 시 Episodic 추출을 시도하지 않는가? Episodic 추출 실패 시 LTM 요약 결과에 영향이 없는가?
   - `strengths`/`weaknesses`/`questions`가 아닌 다른 필드(confidence, source tracking 등)를 추가하지 않았는가 (ADR-007)?
   - 지시 메시지가 `stm_messages`에 저장되지 않고 LLM 호출에만 쓰였는가?
   - 코드펜스로 감싸진 JSON 응답도 정상 파싱되는가 (실제 Gemini 응답 형태 대응)?
3. 결과에 따라 `phases/3-episodic/index.json`의 `step 1` 항목을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary"`에 변경한 파일과 흐름을 한 줄로 요약
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- LTM 요약과 Episodic 추출을 한 번의 `generate()` 호출로 합치지 마라 (ADR-008).
- Episodic 추출 실패(예외/파싱 실패)를 `end_session()` 전체의 실패로 전파시키지 마라 — 반드시 격리해서 무시한다 (ADR-008).
- taxonomy enum, confidence, source_message_ids/turn_indices, occurrence_count, topic_embedding 등을 파싱 결과에 추가하지 마라 (ADR-007).
- 같은 topic을 병합(upsert)하는 로직을 추가하지 마라 (ADR-007 — `episodic.py`는 이미 append-only로 만들어졌다).
- `lossy_clone/__main__.py`를 수정하지 마라 — 이번 phase에서는 CLI 출력에 episodic 저장 결과를 노출하지 않기로 했다 (불필요한 기능 추가 방지, 필요해지면 이후 단계에서 결정).
- `GeminiLLMClient`를 테스트에서 기본으로 사용하지 마라. 테스트는 반드시 `FakeLLMClient`를 주입해서 네트워크 없이 실행되어야 한다.
- `import chatbot`, `import memory`처럼 접두사 없는 절대 import를 쓰지 마라.
- 기존 테스트를 깨뜨리지 마라.
