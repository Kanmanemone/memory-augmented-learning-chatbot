"""Chatbot — 1단계 데이터 흐름을 구현한다.

사용자 입력 -> STM에 사용자 메시지 저장 -> STM에서 최근 대화 이력 읽기
-> 응답 생성 (LLM 호출) -> STM에 응답 저장 -> 응답 반환
"""

import sqlite3
import uuid
from pathlib import Path
from typing import Optional, Union

from lossy_clone.llm import GeminiLLMClient, LLMClient
from lossy_clone.memory.stm import add_message, get_recent_messages, init_stm

_PACKAGE_DIR = Path(__file__).resolve().parent
_DEFAULT_DB_PATH = _PACKAGE_DIR / "data" / "chatbot.db"


class Chatbot:
    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        db_path: Optional[Union[str, Path]] = None,
        session_id: Optional[str] = None,
        history_limit: int = 20,
    ):
        self._llm_client = llm_client if llm_client is not None else GeminiLLMClient()
        self.db_path = Path(db_path) if db_path is not None else _DEFAULT_DB_PATH
        self.session_id = session_id if session_id is not None else str(uuid.uuid4())
        self._history_limit = history_limit

        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def chat(self, message: str) -> str:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            init_stm(conn)
            add_message(conn, session_id=self.session_id, role="user", content=message)

            history = get_recent_messages(conn, session_id=self.session_id, limit=self._history_limit)
            llm_messages = [{"role": row["role"], "content": row["content"]} for row in history]

            reply = self._llm_client.generate(llm_messages)

            add_message(conn, session_id=self.session_id, role="assistant", content=reply)
            return reply
        finally:
            conn.close()
