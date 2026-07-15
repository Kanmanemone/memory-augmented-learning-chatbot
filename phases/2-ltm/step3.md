# Step 3: cli-end-session-wiring

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/PRD.md`
- `/docs/ADR.md` (ADR-006 포함)
- `lossy_clone/__main__.py` — 현재 CLI 루프(exit/quit 문자열, `EOFError`/`KeyboardInterrupt` 종료 경로)
- `lossy_clone/chatbot.py` — step 2에서 추가된 `Chatbot.end_session()`의 실제 시그니처와 반환값
- `lossy_clone/llm.py` — `GeminiLLMClient`가 던질 수 있는 `RuntimeError`(API 키 누락, 네트워크 오류, 응답 포맷 이상)
- `lossy_clone/tests/test_main.py` — `main(llm_client=..., db_path=...)`를 monkeypatch된 `input`으로 구동하는 기존 테스트 패턴

## 작업

`lossy_clone/__main__.py` 수정. CLI 루프가 끝나는 세 경로(`exit`/`quit` 입력, `EOFError`, `KeyboardInterrupt`) 전부에서 `bot.end_session()`을 호출한다. 반환값이 `None`이 아니면 사용자에게 요약이 저장되었음을 알리는 한 줄을 출력한다 (예: `bot> (세션 요약 저장됨: ...)` 형태 — 정확한 문구는 자유).

세 경로 각각에 `end_session()` 호출 코드를 따로 작성하지 말고, 루프를 빠져나가는 지점을 하나로 모으거나 `try`/`finally` 등을 사용해 중복 없이 한 곳에서 처리하라.

`end_session()`은 내부적으로 `LLMClient.generate()`를 호출하므로, API 키 누락/네트워크 오류 시 `GeminiLLMClient`가 `RuntimeError`를 던질 수 있다. 사용자가 `exit`/`Ctrl+D`/`Ctrl+C`로 조용히 종료하려는 순간 이 예외가 그대로 새어나가 트레이스백을 띄우면 안 된다 — `end_session()` 호출을 `try`/`except`로 감싸 실패 시 짧은 한 줄 메시지만 출력하고 정상 종료하라 (예: `bot> (세션 요약 저장 실패: ...)`). 새로운 상태 플래그를 추가하지 않고 `try`/`except`만으로 처리한다.

## 핵심 규칙 (반드시 지켜야 함)

- CLI 종료 경로 세 곳 모두 정확히 한 지점에서 `end_session()`을 호출한다 (중복 코드 금지).
- `end_session()`이 예외를 던져도 CLI 프로세스는 항상 정상 종료해야 한다.
- 이번 step에서 `docs/ARCHITECTURE.md`, `lossy_clone/README.md`를 고치지 마라 — 문서 동기화는 step 4의 몫이다.

## 테스트

`lossy_clone/tests/test_main.py`에 추가:

- `FakeLLMClient(response="...")`를 주입하고 입력을 `["hello", "exit"]`로 monkeypatch한 뒤 `main(...)`을 호출하면, 종료 후 `capsys`로 캡처한 출력에 요약 저장 관련 문구가 포함되어 있는지 (예: `end_session()`이 실제로 호출되어 응답을 출력했는지).
- 대화 없이 바로 `exit`만 입력했을 때(`["exit"]`)는 `end_session()`이 `None`을 반환하므로 요약 관련 출력이 없어야 한다는 케이스도 추가한다.
- `EOFError` 종료 경로(`test_main_stops_on_eof`처럼 `input`이 `EOFError`를 던지는 케이스)에서도 예외 없이 종료되는지 기존 테스트가 계속 통과하는지 확인한다.
- `end_session()`이 예외를 던지는 상황에서도 `main(...)`이 예외 없이 정상 종료되고, 실패를 알리는 한 줄이 출력되는지. **주의**: 입력을 `["exit"]`만으로 구성하면 STM이 비어 `end_session()`이 `generate()`를 아예 호출하지 않고 `None`을 반환해버려 실패 시나리오 자체가 발생하지 않는다 — 반드시 `["hello", "exit"]`처럼 대화를 한 번 넣어 STM을 채운 뒤, `FakeLLMClient`에 주입하는 콜러블이 `messages`에 `role="system"` 메시지가 있을 때만 예외를 던지도록 만들어라 (그래야 일반 `chat()` 호출은 성공하고 `end_session()`의 요약 호출만 실패한다).

## Acceptance Criteria

```bash
python -m compileall -q .   # 구문 오류 없음
python -m pytest            # 테스트 통과
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - CLI 종료 경로 세 곳 모두 `end_session()`을 호출하도록 중복 없이 처리했는가?
   - `end_session()`이 예외를 던져도 CLI가 트레이스백 없이 정상 종료하는가?
3. 결과에 따라 `phases/2-ltm/index.json`의 `step 3` 항목을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary"`에 변경한 파일을 한 줄로 요약
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `__main__.py`에 exit-command 목록 확장(예: "bye", "종료" 추가)이나 `max_turns`/`inactivity_timeout` 판단을 넣지 마라 (ADR-006, PRD 4단계 스코프).
- `docs/ARCHITECTURE.md`, `lossy_clone/README.md`, `lossy_clone/ARCHITECTURE.md`를 고치지 마라 — 문서 동기화는 step 4의 몫이다.
- 기존 테스트를 깨뜨리지 마라.
