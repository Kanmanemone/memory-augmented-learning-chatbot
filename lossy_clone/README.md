# lossy_clone

`memory-augmented-learning-chatbot`(원본)의 "3계층 메모리(STM/LTM/Episodic) 학습 챗봇" 개념을 참고해 처음부터 다시 짠 독립 실행형 챗봇입니다. 이 폴더 하나만 잘라내도 그대로 동작합니다.

## 상태

- ✅ 1단계 — Chatbot 인스턴스 + STM (완료)
- ⬜ 2단계 — LTM (세션 요약 전이)
- ⬜ 3단계 — Episodic memory (주제별 학습 이력 누적)
- ⬜ 4단계 — LTM/Episodic 검색, 반복 질문 감지 등 세부 동작

2~4단계는 아직 구현 전이라 범위/순서가 바뀔 수 있는 예정 로드맵입니다. 단계별 데이터 흐름 다이어그램은 [ARCHITECTURE.md](./ARCHITECTURE.md)에서 확인할 수 있습니다.

## 요구사항

- Python 3
- `GEMINI_API_KEY` 환경변수 (실제 응답을 받으려면 필수. `lossy_clone`은 `os.environ`만 읽고 `.env` 경로를 스스로 찾지 않으므로, 값은 쉘 환경변수나 `.env` 로더 등 원하는 방식으로 넣어주면 됩니다.)

## 빠른 시작

**`lossy_clone/`의 부모 디렉터리** — 지금 이 저장소에서는 `memory-augmented-learning-chatbot/` 최상위 — 에서 실행합니다:

```bash
# GEMINI_API_KEY가 이미 환경변수로 설정되어 있다면 아래 export는 생략
export GEMINI_API_KEY=your-api-key

python -m lossy_clone
```

```
lossy_clone chatbot — 'exit' 또는 'quit'로 종료합니다.
you> Can you explain Python decorators?
bot> (Gemini가 생성한 답변)
you> exit
```

`exit`/`quit` 입력 또는 Ctrl+D(EOF)로 종료합니다. 주고받은 메시지는 `lossy_clone/data/chatbot.db`의 STM에 쌓이고, 같은 세션의 다음 턴 컨텍스트로 재사용됩니다.

## 코드에서 사용

```python
from lossy_clone.chatbot import Chatbot

bot = Chatbot()

print(bot.chat("Can you explain Python decorators?"))
print(bot.chat("I am confused about functools.wraps."))
```

`llm_client`를 주입하면 `GeminiLLMClient` 대신 다른 `LLMClient` 구현체로 교체할 수 있습니다. 테스트에서는 `FakeLLMClient`로 네트워크 없이 검증합니다.

## 주의사항

- `cd lossy_clone`으로 폴더 안까지 들어간 뒤 접두사 없이 `from chatbot import Chatbot`처럼 실행하지 마세요. `lossy_clone/`의 부모 디렉터리에 이름이 같은 원본 모듈(`chatbot.py`, `memory/`)이 있어 그게 잘못 import될 수 있습니다. 항상 부모 디렉터리에서 `python -m lossy_clone` 또는 `from lossy_clone.chatbot import Chatbot`을 사용하세요.
- `lossy_clone/`은 폴더 하나만 잘라내도 동작하도록 설계되어 있습니다. 원본 프로젝트의 다른 파일을 import하지 않습니다.
