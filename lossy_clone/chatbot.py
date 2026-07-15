"""Chatbot — 1단계 데이터 흐름을 구현한다.

사용자 입력 -> STM에 사용자 메시지 저장 -> STM에서 최근 대화 이력 읽기
-> 응답 생성 (LLM 호출) -> STM에 응답 저장 -> 응답 반환
"""

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Optional, Union

from lossy_clone.llm import GeminiLLMClient, LLMClient
from lossy_clone.memory.episodic import init_episodic, save_episodes
from lossy_clone.memory.ltm import init_ltm, save_summary
from lossy_clone.memory.stm import add_message, get_recent_messages, init_stm

_SUMMARY_INSTRUCTION = "위 대화의 핵심 내용을 한국어로 간단히 요약하라."

_EPISODIC_INSTRUCTION = (
    "위 대화에서 다룬 학습 주제를 찾아, "
    "주제별로 사용자가 잘한 점(strengths)/어려워한 점(weaknesses)/질문(questions)을 뽑아라. "
    '코드펜스나 설명 없이 순수 JSON 객체만 출력하라. 형식: '
    '{"topics": [{"topic": "...", "strengths": ["..."], "weaknesses": ["..."], "questions": ["..."]}]}. '
    '다룰 주제가 없으면 {"topics": []}를 출력하라.'
)


def _strip_code_fence(text: str) -> str:
    """```json ... ``` 또는 ``` ... ``` 코드펜스를 벗겨낸다."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.split("\n")
    lines = lines[1:]  # 여는 펜스(```json 또는 ```) 줄 제거
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_episodes(raw_text: str) -> list:
    """LLM 응답에서 topics 배열을 파싱한다. 형식이 안 맞으면 빈 리스트를 반환한다."""
    try:
        payload = json.loads(_strip_code_fence(raw_text))
    except (json.JSONDecodeError, TypeError):
        return []

    if not isinstance(payload, dict):
        return []

    raw_topics = payload.get("topics")
    if not isinstance(raw_topics, list):
        return []

    episodes = []
    for item in raw_topics:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic", "")).strip()
        if not topic:
            continue
        episodes.append(
            {
                "topic": topic,
                "strengths": item.get("strengths") if isinstance(item.get("strengths"), list) else [],
                "weaknesses": item.get("weaknesses") if isinstance(item.get("weaknesses"), list) else [],
                "questions": item.get("questions") if isinstance(item.get("questions"), list) else [],
            }
        )
    return episodes


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

    def end_session(self) -> Optional[str]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            init_stm(conn)
            history = get_recent_messages(conn, session_id=self.session_id, limit=None)
            if not history:
                return None

            history_messages = [{"role": row["role"], "content": row["content"]} for row in history]

            # 지시 메시지는 이력 뒤에 role="user"로 덧붙인다 — STM 이력은 항상 (user, assistant)
            # 쌍으로 끝나므로, 지시를 앞쪽 role="system"으로만 넣으면 Gemini에 보내는 마지막
            # turn이 여전히 "model"로 끝난다. Gemini는 마지막 turn이 이미 model이면 "더
            # 이어 말할 필요 없음"으로 판단해 빈 응답(출력 토큰 0개)을 반환할 수 있다
            # (docs/ADR.md ADR-009). 마지막 turn을 user로 만들어야 실제로 응답을 생성한다.
            summary_messages = history_messages + [{"role": "user", "content": _SUMMARY_INSTRUCTION}]
            summary = self._llm_client.generate(summary_messages)

            init_ltm(conn)
            save_summary(conn, session_id=self.session_id, summary=summary)

            self._extract_and_save_episodes(conn, history_messages)

            return summary
        finally:
            conn.close()

    def _extract_and_save_episodes(self, conn: sqlite3.Connection, history_messages: list) -> None:
        init_episodic(conn)

        episodic_messages = history_messages + [{"role": "user", "content": _EPISODIC_INSTRUCTION}]
        try:
            raw_response = self._llm_client.generate(episodic_messages)
            episodes = _parse_episodes(raw_response)
        except Exception:
            return

        if episodes:
            save_episodes(conn, session_id=self.session_id, episodes=episodes)
