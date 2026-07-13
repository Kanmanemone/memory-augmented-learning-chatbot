# Step 2: llm-interface

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/PRD.md`
- `/docs/ADR.md` (특히 ADR-004: 실제로 동작하되 특정 벤더 SDK에 고정되지 않는 교체 가능한 LLM 연동)
- `lossy_clone/__init__.py`, `lossy_clone/requirements.txt` — Step 0에서 생성된 폴더 뼈대
- `lossy_clone/tests/conftest.py` — Step 1에서 추가된 import 충돌 가드. 이 가드가 자동 적용되므로, 이 step의 코드/테스트는 항상 `lossy_clone.` 접두사 또는 패키지 내부 상대 import만 사용해야 한다.

이 step은 **인터페이스와 테스트용 fake 구현체만** 다룬다. 실제로 네트워크를 호출하는 Gemini 구현체는 다음 step(`llm-gemini-client`)에서 추가한다.

## 작업

`lossy_clone/llm.py`를 새로 만든다.

### 인터페이스

```python
from abc import ABC, abstractmethod

class LLMClient(ABC):
    """교체 가능한 LLM 연동 지점. 벤더별 구현은 이 인터페이스만 따르면 된다."""

    @abstractmethod
    def generate(self, messages: list[dict[str, str]]) -> str:
        """messages: [{"role": "user"|"assistant"|"system", "content": str}, ...]
        마지막 응답 텍스트를 반환한다."""
        ...
```

### `FakeLLMClient(LLMClient)`

- 테스트 전용 deterministic 구현체. 네트워크를 전혀 사용하지 않는다.
- 생성자에서 고정 응답 문자열이나 응답 생성 함수(`Callable[[list[dict]], str]`)를 선택적으로 주입받을 수 있게 하고, 기본값은 입력 `messages`에서 확인 가능한 결정적 문자열(예: 마지막 user 메시지를 그대로 포함하는 echo 형태)을 반환한다.
- `generate()`가 호출될 때마다 전달받은 `messages`를 인스턴스 속성(예: `self.received_calls`, 매 호출의 `messages`를 순서대로 담는 리스트)에 기록해서, 이후 Step(`chatbot`)의 테스트가 "어떤 대화 컨텍스트가 LLM에 전달됐는지" 검증할 수 있게 한다.

### 테스트

`lossy_clone/tests/test_llm.py`를 먼저 작성하고 통과하는 구현을 만들어라 (TDD).

- `LLMClient`가 추상 클래스라 직접 인스턴스화할 수 없는지 (`generate`를 구현하지 않은 서브클래스는 인스턴스화 시 `TypeError`).
- `FakeLLMClient()` 기본 동작: `generate([{"role": "user", "content": "hi"}])` 호출 시 deterministic한 문자열을 반환하는지, 그 문자열에 입력 내용이 반영되는지.
- 커스텀 응답을 주입했을 때(고정 문자열 또는 함수) 그 값이 그대로 반환되는지.
- `generate()`를 여러 번 호출한 뒤 `received_calls`에 각 호출의 `messages`가 순서대로, 정확히 기록되는지.

## Acceptance Criteria

```bash
python -m compileall -q .   # 구문 오류 없음
python -m pytest            # 테스트 통과
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `ADR.md`(ADR-004 교체 가능한 인터페이스)를 벗어나지 않았는가?
   - `CLAUDE.md` CRITICAL 규칙(`lossy_clone/` 밖 파일 미참조, 벤더 SDK 비의존)을 위반하지 않았는가?
3. 결과에 따라 `phases/1-chatbot-stm/index.json`의 `step 2` 항목을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary"`에 생성한 파일과 `LLMClient`/`FakeLLMClient` 시그니처를 한 줄로 요약
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `GeminiLLMClient`나 그 외 실제 네트워크를 호출하는 구현체를 이 step에서 만들지 마라. 다음 step(`llm-gemini-client`)에서 다룬다.
- `requests`나 다른 HTTP 라이브러리를 `requirements.txt`에 추가하지 마라. 이 step은 네트워크가 필요 없다.
- `google-genai` 등 벤더 SDK를 코드나 `requirements.txt`에 추가하지 마라 (ADR-004).
- `import chatbot`, `import memory`처럼 접두사 없는 절대 import를 쓰지 마라. Step 1의 가드가 이를 감지해 테스트를 실패시킨다.
- STM, `Chatbot` 클래스 등 이후 단계의 기능을 앞당겨 구현하지 마라.
- 기존 테스트를 깨뜨리지 마라.
