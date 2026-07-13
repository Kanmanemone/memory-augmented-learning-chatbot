# Step 3: llm-gemini-client

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/PRD.md`
- `/docs/ADR.md` (특히 ADR-001 독립성, ADR-004 실제 동작하는 벤더 교체 가능 LLM 연동)
- `lossy_clone/llm.py` — Step 2에서 만든 `LLMClient` 추상 인터페이스와 `FakeLLMClient`. 실제 시그니처를 코드에서 직접 확인하라 (이 문서의 설명과 미묘하게 다를 수 있다).
- `lossy_clone/requirements.txt`, `lossy_clone/tests/conftest.py`

## 배경

`lossy_clone`의 LLM 연동은 장식적인 mock이 아니라 실제로 호출되어 동작해야 하지만, 특정 벤더 SDK에 직접 의존하지 않고 교체 가능한 인터페이스로 분리해야 한다 (ADR-004). 이 프로젝트는 기본 구현체로 **Google Gemini를 SDK 없이 순수 REST 호출**로 연동하기로 결정했다 (`google-genai` 등 벤더 SDK를 `requirements.txt`에 추가하지 않는다). API 키는 환경변수 `GEMINI_API_KEY`로 전달되며, 저장소 루트에 `.env` 파일이 이미 존재하고 로컬 실행 시 값이 채워져 있다.

## 작업

`lossy_clone/llm.py`에 `GeminiLLMClient(LLMClient)`를 추가한다 (Step 2에서 만든 `LLMClient`를 그대로 상속).

### 구현

- Gemini REST API의 `generateContent` 엔드포인트를 `requests`(또는 표준 라이브러리 `urllib.request`)로 직접 호출한다.
  엔드포인트: `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}`
- 생성자에서 `api_key: str | None = None`, `model: str | None = None`을 받는다. `api_key`가 없으면 `os.environ["GEMINI_API_KEY"]`를 읽는다. `model`이 없으면 환경변수 `GEMINI_MODEL`을 읽고, 그것도 없으면 구현 시점에 유효한 기본 Gemini 모델(예: `gemini-2.5-flash`)을 사용한다.
- API 키가 비어 있으면(`""` 포함) `generate()` 호출 시점에 명확한 예외(`RuntimeError` 등)를 던진다. 생성자에서는 던지지 않는다 (인스턴스화 자체는 키 없이도 가능해야 테스트에서 조합하기 쉽다).
- `messages`를 Gemini API의 `contents` 포맷으로 변환해 요청한다 (`role`이 `assistant`이면 Gemini의 `model` role로 매핑하고, `system` 메시지는 Gemini의 `systemInstruction` 필드로 보내거나 별도 처리한다 — 정확한 매핑 방식은 구현 재량).
- 응답에서 텍스트를 추출해 반환한다. 네트워크 오류나 비정상 응답(HTTP status, 예상과 다른 JSON 구조)은 명확한 예외로 전파한다 (조용히 빈 문자열을 반환하지 마라).
- `.env` 파일 경로를 코드에 하드코딩하지 마라 (`os.environ`만 읽는다). `.env`를 실제 프로세스 환경에 로드하는 것은 실행 환경(쉘, `python-dotenv` 등)의 책임이며 이 클래스의 책임이 아니다.

### requirements.txt

`lossy_clone/requirements.txt`에 `requests` 한 줄을 추가한다 (표준 라이브러리 `urllib.request`로 구현했다면 이 줄은 생략 가능 — 실제로 사용한 방식에 맞춰라).

### 테스트

`lossy_clone/tests/test_llm.py`에 이어서 작성하라 (또는 `lossy_clone/tests/test_llm_gemini.py`로 분리해도 된다). 먼저 테스트를 작성하고 통과하는 구현을 만들어라 (TDD).

- **네트워크 mock 단위 테스트 (항상 실행됨, 핵심 검증 대상)**: `unittest.mock`/`pytest monkeypatch`로 실제 HTTP 호출(`requests.post` 등)을 가짜로 대체해서, 네트워크 없이 아래를 검증한다.
  - 요청 URL에 올바른 모델명과 API 키가 들어가는지.
  - `messages`가 Gemini `contents` 포맷으로 올바르게 변환되는지.
  - 정상 응답 JSON을 주면 텍스트를 올바르게 추출해 반환하는지.
  - 비정상 응답(예: HTTP 4xx/5xx, 예상과 다른 JSON 구조)을 주면 명확한 예외가 발생하는지.
  - `api_key`가 빈 문자열이거나 없을 때 `generate()` 호출 시 예외가 발생하는지 (생성자는 통과해야 함).
- **실제 API 호출 통합 테스트 (조건부 실행)**: **다음 두 조건이 모두 만족할 때만** 실행하고, 아니면 `pytest.skip`한다.
  1. `os.environ.get("GEMINI_API_KEY")`가 비어있지 않음
  2. 명시적 opt-in 환경변수 `RUN_LLM_INTEGRATION=1`이 설정되어 있음

  두 번째 조건이 반드시 필요한 이유: 이 저장소의 `.claude/settings.json`에 등록된 Stop hook이 **매 응답마다 자동으로 `python -m pytest`를 실행**한다. `GEMINI_API_KEY`가 있다는 이유만으로 실제 네트워크 호출 테스트를 무조건 실행하면, 매 응답마다 실제 API 요청과 비용이 발생하고, 샌드박스 환경에서 네트워크가 차단되어 있으면 항상 테스트가 실패한다. 반드시 별도 opt-in 플래그로 막아라.

## Acceptance Criteria

```bash
python -m compileall -q .   # 구문 오류 없음
python -m pytest            # 테스트 통과 (RUN_LLM_INTEGRATION 미설정 상태 기준)
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. 이때 `RUN_LLM_INTEGRATION` 환경변수를 설정하지 않은 기본 상태에서 통과해야 한다.
2. 아키텍처 체크리스트를 확인한다:
   - `ADR.md`의 "특정 LLM 벤더 SDK 비의존" 원칙(ADR-004)을 지켰는가 — `requirements.txt`에 `google-genai` 등 벤더 SDK가 없는지 확인.
   - `CLAUDE.md` CRITICAL 규칙(`lossy_clone/` 밖 파일 미참조, LLM 벤더 직접 의존 금지)을 위반하지 않았는가?
3. 결과에 따라 `phases/1-chatbot-stm/index.json`의 `step 3` 항목을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary"`에 엔드포인트, 기본 모델명, mock 테스트 방식을 한 줄로 요약
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `google-genai`, `openai` 등 벤더 SDK를 `requirements.txt`나 코드에 추가하지 마라. REST/HTTP로만 호출한다.
- `.env` 파일의 실제 API 키 값을 로그, 커밋 메시지, summary, 테스트 코드에 하드코딩하거나 출력하지 마라.
- `GEMINI_API_KEY`가 있다고 해서 opt-in 플래그(`RUN_LLM_INTEGRATION`) 없이 실제 네트워크 호출 테스트를 기본 실행되게 만들지 마라.
- mock 없이 매 테스트 실행마다 실제 네트워크를 타는 테스트만으로 이 step의 핵심 로직(요청 포맷팅, 응답 파싱, 에러 처리)을 검증하려 하지 마라. 네트워크가 없거나 API 키가 없어도 핵심 로직은 mock으로 항상 검증돼야 한다.
- `import chatbot`, `import memory`처럼 접두사 없는 절대 import를 쓰지 마라.
- STM, `Chatbot` 클래스 등 이후 단계의 기능을 앞당겨 구현하지 마라.
- Step 2에서 만든 `LLMClient`, `FakeLLMClient`를 변경하지 마라 (인터페이스를 깨뜨리면 안 됨). 필요하면 `GeminiLLMClient`만 추가하라.
- 기존 테스트를 깨뜨리지 마라.
