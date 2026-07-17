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
from lossy_clone.memory.episodic import init_episodic, save_episodes, search_episodic
from lossy_clone.memory.ltm import init_ltm, save_summary, search_ltm
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


def _as_str_list(value) -> list:
    """value가 list면 각 원소를 str로 강제하고, 아니면 빈 리스트를 반환한다.

    ADR-008이 문서화한 응답 계약(strengths/weaknesses/questions는 리스트의
    각 원소가 str)을 LLM이 항상 지킨다고 보장할 수 없어(예: 숫자가 섞여 옴),
    topic과 동일하게 이 경계에서 str로 강제한다.
    """
    if not isinstance(value, list):
        return []
    return [str(v) for v in value]


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
                "strengths": _as_str_list(item.get("strengths")),
                "weaknesses": _as_str_list(item.get("weaknesses")),
                "questions": _as_str_list(item.get("questions")),
            }
        )
    return episodes


def build_memory_context(ltm_hits: list, episodic_hits: list) -> Optional[str]:
    """검색된 LTM/Episodic 결과를 하나의 컨텍스트 텍스트로 조립한다.

    ltm_hits, episodic_hits가 둘 다 비어있으면 None을 반환한다. DB나 LLM을
    전혀 모르는 순수 함수다 (ADR-011).
    """
    if not ltm_hits and not episodic_hits:
        return None

    lines = [
        "다음은 사용자의 과거 학습 이력이다. 지금 질문과 관련 있으면 참고해서 답하라. "
        "특히 지금 질문이 과거 질문과 비슷하면 그 점을 언급하고 이어서 설명하라."
    ]

    if ltm_hits:
        lines.append("과거 세션 요약:")
        for hit in ltm_hits:
            lines.append(f"- {hit['summary']}")

    if episodic_hits:
        lines.append("과거에 다룬 주제:")
        for hit in episodic_hits:
            lines.append(f"- 주제: {hit['topic']}")
            if hit.get("questions"):
                lines.append(f"  과거 질문: {', '.join(hit['questions'])}")
            if hit.get("strengths"):
                lines.append(f"  잘한 점: {', '.join(hit['strengths'])}")
            if hit.get("weaknesses"):
                lines.append(f"  어려워한 점: {', '.join(hit['weaknesses'])}")

    return "\n".join(lines)


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

            # 과거 LTM/Episodic 중 이번 메시지와 관련 있는 내용을 찾아 system 메시지로
            # 이력 앞에 붙인다. STM 이력의 마지막은 항상 방금 저장한 사용자 메시지이므로
            # (ADR-009), system 메시지를 앞에 붙여도 Gemini에 보내는 마지막 turn은
            # 그대로 user로 유지된다 (docs/ADR.md ADR-011).
            ltm_hits = search_ltm(conn, query=message)
            episodic_hits = search_episodic(conn, query=message)
            memory_context = build_memory_context(ltm_hits, episodic_hits)

            history = get_recent_messages(conn, session_id=self.session_id, limit=self._history_limit)
            llm_messages = [{"role": row["role"], "content": row["content"]} for row in history]
            if memory_context is not None:
                llm_messages = [{"role": "system", "content": memory_context}] + llm_messages

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
