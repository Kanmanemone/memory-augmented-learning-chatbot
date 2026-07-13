"""저장소 루트에서 `python -m lossy_clone`으로 실행하는 대화형 데모.

`cd lossy_clone`으로 들어가서 접두사 없이 import하는 방식은 저장소 루트의
원본 모듈(chatbot.py, memory/)과 이름이 겹쳐 잘못 import될 위험이 있어
(tests/conftest.py 참고) 쓰지 않는다. `python -m lossy_clone`은 항상
`lossy_clone.` 패키지 경로로 import하므로 그 위험이 없다.
"""

from pathlib import Path
from typing import Optional, Union

from lossy_clone.chatbot import Chatbot
from lossy_clone.llm import LLMClient

_EXIT_COMMANDS = {"exit", "quit"}


def main(llm_client: Optional[LLMClient] = None, db_path: Optional[Union[str, Path]] = None) -> None:
    bot = Chatbot(llm_client=llm_client, db_path=db_path)
    print("lossy_clone chatbot — 'exit' 또는 'quit'로 종료합니다.")

    while True:
        try:
            message = input("you> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if message.strip().lower() in _EXIT_COMMANDS:
            break

        reply = bot.chat(message)
        print(f"bot> {reply}")


if __name__ == "__main__":
    main()
