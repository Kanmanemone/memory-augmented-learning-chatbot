# lossy_clone

`memory-augmented-learning-chatbot`(원본)의 "3계층 메모리(STM/LTM/Episodic) 학습 챗봇" 개념을 참고해 처음부터 다시 짠 독립 실행형 챗봇입니다. 이 폴더 하나만 잘라내도 그대로 동작합니다.

## 상태

- ✅ 1단계 — Chatbot 인스턴스 + STM (완료)
- ✅ 2단계 — LTM (세션 요약 전이) (완료)
- ✅ 3단계 — Episodic memory (주제별 학습 이력 누적) (완료)
- ✅ 4단계 — LTM/Episodic 검색, 통합 컨텍스트 구성 (완료)

`docs/PRD.md`의 로드맵 4단계가 모두 끝났습니다. 단계별 데이터 흐름 다이어그램은 [ARCHITECTURE.md](./ARCHITECTURE.md)에서 확인할 수 있습니다.

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
bot> (세션 요약 저장됨: Gemini가 생성한 요약)
```

`exit`/`quit` 입력 또는 Ctrl+D(EOF)로 종료합니다. 주고받은 메시지는 `lossy_clone/data/chatbot.db`의 STM에 쌓이고, 같은 세션의 다음 턴 컨텍스트로 재사용됩니다. 매 턴 과거 LTM/Episodic 중 지금 질문과 관련 있는 내용도 자동으로 찾아 답변에 참고합니다(조용히, CLI 출력 없이). CLI가 종료될 때 세션 전체 대화가 요약되어 같은 파일의 LTM(`ltm` 테이블)에 저장되고, 성공하면 `bot> (세션 요약 저장됨: ...)`이 출력됩니다. 같은 시점에 세션에서 다룬 주제별 강점/약점/질문도 Episodic(`episodic` 테이블)에 조용히(CLI 출력 없이) 저장됩니다.

## 코드에서 사용

```python
from lossy_clone.chatbot import Chatbot

bot = Chatbot()

print(bot.chat("Can you explain Python decorators?"))
print(bot.chat("I am confused about functools.wraps."))
# chat()은 매 턴 과거 LTM/Episodic 중 이번 메시지와 관련 있는 내용을 자동으로 찾아
# 응답 생성 시 참고한다 (임베딩 없이 키워드 겹침만 사용, 관련 내용이 없으면 그냥 무시됨).

summary = bot.end_session()  # STM 전체를 요약해 LTM에 저장하고, 요약 텍스트를 반환한다
print(summary)
# 같은 호출 안에서 세션에서 다룬 주제별 강점/약점/질문도 Episodic(`episodic` 테이블)에 저장된다
# (반환값에는 포함되지 않는 side effect).
```

`llm_client`를 주입하면 `GeminiLLMClient` 대신 다른 `LLMClient` 구현체로 교체할 수 있습니다. 테스트에서는 `FakeLLMClient`로 네트워크 없이 검증합니다.

## 주의사항

- `cd lossy_clone`으로 폴더 안까지 들어간 뒤 접두사 없이 `from chatbot import Chatbot`처럼 실행하지 마세요. `lossy_clone/`의 부모 디렉터리에 이름이 같은 원본 모듈(`chatbot.py`, `memory/`)이 있어 그게 잘못 import될 수 있습니다. 항상 부모 디렉터리에서 `python -m lossy_clone` 또는 `from lossy_clone.chatbot import Chatbot`을 사용하세요.
- `lossy_clone/`은 폴더 하나만 잘라내도 동작하도록 설계되어 있습니다. 원본 프로젝트의 다른 파일을 import하지 않습니다.
