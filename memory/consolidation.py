"""
Session-end memory consolidation.

This module connects a session-end event to the post-memory processing loop:
STM messages are read, summarized into LTM, projected into topic-level
Episodic memory, then cleared from STM after successful persistence.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    from google import genai
except ImportError:
    genai = None

from episodic_schema import (
    CHROMA_DIR,
    DB_PATH,
    create_episodic_table,
    normalize_topic_tags,
    upsert_episodic_record,
)
from memory import ltm
from memory.session_manager import SessionEndEvent
from memory.stm import delete_messages_by_ids, get_recent_messages


DEFAULT_EMBEDDING_DIMENSION = 3
DEFAULT_GEMINI_SUMMARY_MODEL = "gemini-2.5-pro"
DEFAULT_GEMINI_LTM_TO_EPISODIC_MODEL = "gemini-2.5-pro"
GEMINI_ENV_PATH = Path.home() / ".env"
logger = logging.getLogger(__name__)


DDL_MEMORY_STATE = """
CREATE TABLE IF NOT EXISTS memory_state (
    session_id TEXT PRIMARY KEY,
    stm_status TEXT NOT NULL,
    ltm_status TEXT NOT NULL,
    stm_cursor INTEGER NOT NULL DEFAULT -1,
    last_transferred_turn_index INTEGER,
    transferred_message_count INTEGER NOT NULL DEFAULT 0,
    deleted_stm_rows INTEGER NOT NULL DEFAULT 0,
    ltm_id TEXT,
    last_stm_timestamp TEXT,
    consolidated_at TEXT,
    updated_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class ConsolidationAnalysis:
    """Structured output consumed by the STM-to-memory persistence loop."""

    summary: str
    struggles: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    confusions: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    embedding: list[float] | None = None


@dataclass(frozen=True)
class EpisodicConversion:
    """Structured LTM-to-Episodic output before persistence."""

    topics: list[str] = field(default_factory=list)
    topic_tags: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    embedding: list[float] | None = None


Analyzer = Callable[[list[dict]], ConsolidationAnalysis]
EmbeddingFn = Callable[[str], list[float]]
EpisodicConverter = Callable[[ConsolidationAnalysis, list[dict]], EpisodicConversion]


def init_memory_state(conn: sqlite3.Connection) -> None:
    """Create the durable consolidation state table."""
    conn.execute(DDL_MEMORY_STATE)
    conn.commit()


def get_memory_state(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """Return persisted STM/LTM transfer metadata for a session."""
    init_memory_state(conn)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM memory_state WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _record_memory_state(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    stm_status: str,
    ltm_status: str,
    stm_cursor: int = -1,
    last_transferred_turn_index: int | None = None,
    transferred_message_count: int = 0,
    deleted_stm_rows: int = 0,
    ltm_id: str | None = None,
    last_stm_timestamp: str | None = None,
    consolidated_at: str | None = None,
) -> None:
    init_memory_state(conn)
    updated_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO memory_state (
            session_id, stm_status, ltm_status, stm_cursor,
            last_transferred_turn_index, transferred_message_count,
            deleted_stm_rows, ltm_id, last_stm_timestamp, consolidated_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            stm_status = excluded.stm_status,
            ltm_status = excluded.ltm_status,
            stm_cursor = excluded.stm_cursor,
            last_transferred_turn_index = excluded.last_transferred_turn_index,
            transferred_message_count = excluded.transferred_message_count,
            deleted_stm_rows = excluded.deleted_stm_rows,
            ltm_id = COALESCE(excluded.ltm_id, memory_state.ltm_id),
            last_stm_timestamp = COALESCE(
                excluded.last_stm_timestamp,
                memory_state.last_stm_timestamp
            ),
            consolidated_at = COALESCE(
                excluded.consolidated_at,
                memory_state.consolidated_at
            ),
            updated_at = excluded.updated_at
        """,
        (
            session_id,
            stm_status,
            ltm_status,
            stm_cursor,
            last_transferred_turn_index,
            transferred_message_count,
            deleted_stm_rows,
            ltm_id,
            last_stm_timestamp,
            consolidated_at,
            updated_at,
        ),
    )
    conn.commit()


def _message_text(messages: Iterable[dict]) -> str:
    return "\n".join(
        f"{message.get('role', 'unknown')}: {message.get('content', '')}"
        for message in messages
    )


def _load_gemini_api_key(env_path: Path = GEMINI_ENV_PATH) -> str | None:
    """Load GEMINI_API_KEY from env, ~/.env, or the project .env."""
    existing_value = os.environ.get("GEMINI_API_KEY")
    if existing_value:
        return existing_value

    candidate_paths = [Path(env_path)]
    project_env_path = Path.cwd() / ".env"
    if project_env_path not in candidate_paths:
        candidate_paths.append(project_env_path)

    for candidate_path in candidate_paths:
        if not candidate_path.exists():
            continue
        for line in candidate_path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "GEMINI_API_KEY":
                return value.strip().strip('"').strip("'") or None
    return None


def _get_value(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _extract_response_text(response: Any) -> str:
    text = _get_value(response, "text")
    if isinstance(text, str):
        return text
    candidates = _get_value(response, "candidates") or []
    for candidate in candidates:
        content = _get_value(candidate, "content")
        parts = _get_value(content, "parts") or []
        for part in parts:
            part_text = _get_value(part, "text")
            if isinstance(part_text, str):
                return part_text
    return ""


def _parse_gemini_analysis(raw_text: str) -> dict:
    stripped = raw_text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if match is None:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Gemini consolidation response must be a JSON object")
    return parsed


def _first_present(parsed: dict, *keys: str) -> Any:
    for key in keys:
        if key in parsed:
            return parsed[key]
    return None


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _coerce_str_list_or_scalar(value: Any) -> list[str]:
    if isinstance(value, list):
        return _coerce_str_list(value)
    if value is None:
        return []
    item = str(value).strip()
    return [item] if item else []


def _coerce_embedding(value: Any) -> list[float] | None:
    if not isinstance(value, list):
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def _build_gemini_consolidation_prompt(messages: list[dict]) -> str:
    return (
        "Summarize this learning chatbot session into the existing memory schema. "
        "Return only valid JSON matching this LTM payload: summary, struggles, "
        "strengths, confusions, topic_tags, questions, embedding. Use topic_tags "
        "for the same topic-tag values persisted by the current LTM schema. The "
        "embedding must be a short numeric list compatible with the current demo "
        "vector store.\n\n"
        f"Session transcript:\n{_message_text(messages)}"
    )


def _join_prompt_list(values: list[str]) -> str:
    return ", ".join(values) if values else "none"


def _build_gemini_ltm_to_episodic_prompt(
    analysis: ConsolidationAnalysis,
    messages: list[dict],
) -> str:
    return (
        "Convert this LTM learning summary into the existing Episodic memory "
        "schema. Preserve the current request variables and return only valid "
        "JSON with keys: topics, topic_tags, strengths, weaknesses, questions, "
        "embedding. Use topic_tags in the existing category:topic-slug format.\n\n"
        "Extract the existing Episodic schema fields: topic, strengths, "
        "weaknesses, questions. Return topic evidence through the topics and "
        "topic_tags JSON keys so the current public schema and persistence code "
        "remain unchanged.\n\n"
        f"LTM summary: {analysis.summary}\n"
        f"Struggles: {_join_prompt_list(analysis.struggles)}\n"
        f"Strengths: {_join_prompt_list(analysis.strengths)}\n"
        f"Confusions: {_join_prompt_list(analysis.confusions)}\n"
        f"Topics: {_join_prompt_list(analysis.topics)}\n"
        f"Questions: {_join_prompt_list(analysis.questions)}\n\n"
        f"Session transcript:\n{_message_text(messages)}"
    )


def _extract_questions(messages: list[dict]) -> list[str]:
    questions: list[str] = []
    for message in messages:
        if message.get("role") != "user":
            continue
        content = str(message.get("content", "")).strip()
        if "?" in content:
            questions.append(content)
    return questions


def _infer_topics(text: str) -> list[str]:
    lowered = text.lower()
    keyword_topics = (
        ("recursion", ("recursion", "recursive", "base case")),
        ("python decorators", ("decorator", "functools.wraps", "@wraps")),
        ("sqlite", ("sqlite", "sql", "table", "schema")),
        ("embeddings", ("embedding", "vector", "chroma", "faiss")),
    )
    topics = [
        topic
        for topic, keywords in keyword_topics
        if any(keyword in lowered for keyword in keywords)
    ]
    return topics or ["general:conversation"]


def _default_embedding(text: str) -> list[float]:
    """Small deterministic embedding for offline demo and test environments."""
    lowered = text.lower()
    signals = (
        ("recursion", "recursive", "base case"),
        ("sqlite", "sql", "schema", "table"),
        ("embedding", "vector", "chroma", "faiss"),
    )
    vector = [
        float(sum(lowered.count(token) for token in signal_group))
        for signal_group in signals
    ]
    if not any(vector):
        vector[0] = 1.0
    return vector[:DEFAULT_EMBEDDING_DIMENSION]


def _has_ltm_for_session(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    db_path: Path | None = None,
) -> bool:
    """
    Return True when this session has already produced a persisted LTM row.

    The in-memory processed-session set protects one consolidator instance.
    This persisted check protects the same STM rows from being re-summarized
    by a fresh consolidator when STM is retained for demos or inspection.
    """
    if db_path is None:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ltm'"
        ).fetchone()
        if table is None:
            return False
        row = conn.execute(
            "SELECT 1 FROM ltm WHERE session_id = ? LIMIT 1",
            (session_id,),
        ).fetchone()
        return row is not None

    if not db_path.exists():
        return False

    with sqlite3.connect(db_path) as ltm_conn:
        table = ltm_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ltm'"
        ).fetchone()
        if table is None:
            return False
        row = ltm_conn.execute(
            "SELECT 1 FROM ltm WHERE session_id = ? LIMIT 1",
            (session_id,),
        ).fetchone()
    return row is not None


def default_analyzer(messages: list[dict]) -> ConsolidationAnalysis:
    """
    Build a Gemini-backed consolidation analysis for STM-to-LTM transfer.

    Tests and offline demos can still inject an analyzer with the same output
    shape; when Gemini credentials are unavailable this keeps the prototype
    inspectable with the deterministic fallback.
    """
    api_key = _load_gemini_api_key()
    if genai is not None and api_key:
        prompt = _build_gemini_consolidation_prompt(messages)
        response = genai.Client(api_key=api_key).models.generate_content(
            model=DEFAULT_GEMINI_SUMMARY_MODEL,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "temperature": 0.2,
            },
        )
        parsed = _parse_gemini_analysis(_extract_response_text(response))
        return ConsolidationAnalysis(
            summary=str(parsed.get("summary", "")).strip() or "Empty session.",
            struggles=_coerce_str_list(parsed.get("struggles")),
            strengths=_coerce_str_list(parsed.get("strengths")),
            confusions=_coerce_str_list(parsed.get("confusions")),
            topics=_coerce_str_list(_first_present(parsed, "topic_tags", "topics")),
            questions=_coerce_str_list(parsed.get("questions")),
            embedding=_coerce_embedding(parsed.get("embedding")),
        )

    transcript = _message_text(messages)
    compact = re.sub(r"\s+", " ", transcript).strip()
    summary = compact[:500] if compact else "Empty session."
    lowered = transcript.lower()

    struggles: list[str] = []
    strengths: list[str] = []
    confusions: list[str] = []

    if any(token in lowered for token in ("confused", "don't understand", "why", "?")):
        confusions.append("needs clarification")
    if any(token in lowered for token in ("struggle", "hard", "stuck", "error")):
        struggles.append("reported difficulty")
    if any(token in lowered for token in ("understand", "got it", "thanks", "example")):
        strengths.append("engages with examples")

    return ConsolidationAnalysis(
        summary=summary,
        struggles=struggles,
        strengths=strengths,
        confusions=confusions,
        topics=_infer_topics(transcript),
        questions=_extract_questions(messages),
        embedding=_default_embedding(transcript),
    )


def _fallback_episodic_conversion(
    analysis: ConsolidationAnalysis,
) -> EpisodicConversion:
    return EpisodicConversion(
        topics=analysis.topics,
        topic_tags=normalize_topic_tags(analysis.topics or ["general:conversation"]),
        strengths=analysis.strengths,
        weaknesses=analysis.struggles,
        questions=analysis.questions,
        embedding=analysis.embedding,
    )


def default_ltm_to_episodic_converter(
    analysis: ConsolidationAnalysis,
    messages: list[dict],
) -> EpisodicConversion:
    """
    Convert an LTM summary into Gemini-structured Episodic memory fields.

    The fallback preserves the existing in-process mapping so tests and offline
    demos do not require Gemini credentials.
    """
    api_key = _load_gemini_api_key()
    if genai is not None and api_key:
        try:
            prompt = _build_gemini_ltm_to_episodic_prompt(analysis, messages)
            response = genai.Client(api_key=api_key).models.generate_content(
                model=DEFAULT_GEMINI_LTM_TO_EPISODIC_MODEL,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "temperature": 0.2,
                },
            )
            parsed = _parse_gemini_analysis(_extract_response_text(response))
            topics = _coerce_str_list_or_scalar(_first_present(parsed, "topics", "topic"))
            topic_tags = _coerce_str_list_or_scalar(parsed.get("topic_tags"))
            return EpisodicConversion(
                topics=topics,
                topic_tags=normalize_topic_tags(topic_tags or topics or analysis.topics),
                strengths=_coerce_str_list(parsed.get("strengths")),
                weaknesses=_coerce_str_list(
                    parsed.get("weaknesses", parsed.get("struggles"))
                ),
                questions=_coerce_str_list(parsed.get("questions")),
                embedding=_coerce_embedding(
                    _first_present(parsed, "embedding", "topic_embedding")
                ),
            )
        except Exception as exc:
            logger.warning(
                "Gemini LTM-to-Episodic conversion failed; using deterministic fallback: %s",
                exc,
            )

    return _fallback_episodic_conversion(analysis)


class SessionMemoryConsolidator:
    """Callable adapter that can be registered on a session-end event."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        db_path: str | Path | None = None,
        chroma_dir: str | Path | None = None,
        analyzer: Analyzer = default_analyzer,
        episodic_converter: EpisodicConverter = default_ltm_to_episodic_converter,
        embedding_fn: EmbeddingFn | None = None,
        stm_window: int = 20,
        clear_stm: bool = True,
    ) -> None:
        self.conn = conn
        self.db_path = Path(db_path) if db_path is not None else None
        self.chroma_dir = Path(chroma_dir) if chroma_dir is not None else None
        self.analyzer = analyzer
        self.episodic_converter = episodic_converter
        self.embedding_fn = embedding_fn
        self.stm_window = stm_window
        self.clear_stm = clear_stm
        self._processed_session_ids: set[str] = set()

    def handle_session_end(self, event: SessionEndEvent) -> dict:
        """Run consolidation for the ended session exactly once."""
        return self.consolidate(event.session_id)

    def consolidate(self, session_id: str) -> dict:
        """Persist STM-derived LTM and Episodic records for one session."""
        if session_id in self._processed_session_ids:
            return {"session_id": session_id, "status": "already_processed"}

        ltm_db_path = self.db_path if self.db_path is not None else ltm.DB_PATH
        if _has_ltm_for_session(self.conn, session_id, db_path=ltm_db_path):
            self._processed_session_ids.add(session_id)
            _record_memory_state(
                self.conn,
                session_id=session_id,
                stm_status="already_processed",
                ltm_status="exists",
            )
            return {"session_id": session_id, "status": "already_processed"}

        messages = get_recent_messages(self.conn, session_id, n=self.stm_window)
        if not messages:
            self._processed_session_ids.add(session_id)
            _record_memory_state(
                self.conn,
                session_id=session_id,
                stm_status="empty",
                ltm_status="not_created",
            )
            return {"session_id": session_id, "status": "no_messages"}

        analysis = self.analyzer(messages)
        embedding = analysis.embedding
        if embedding is None and self.embedding_fn is not None:
            embedding = self.embedding_fn(analysis.summary)
        if embedding is None:
            embedding = _default_embedding(analysis.summary)

        episodic_db_path = self.db_path if self.db_path is not None else DB_PATH
        episodic_chroma_dir = (
            self.chroma_dir if self.chroma_dir is not None else CHROMA_DIR
        )

        ltm_chroma_dir = (
            self.chroma_dir if self.chroma_dir is not None else ltm.CHROMA_PATH
        )

        ltm.init_ltm(
            conn=self.conn,
            ensure_vector_collection=True,
            chroma_path=ltm_chroma_dir,
        )
        create_episodic_table(episodic_db_path)
        ltm_id = ltm.save_ltm_summary(
            ltm.LTMSummaryInput(
                session_id=session_id,
                summary=analysis.summary,
                struggles=analysis.struggles,
                strengths=analysis.strengths,
                confusions=analysis.confusions,
                embedding=embedding,
                topic_tags=analysis.topics,
            ),
            db_path=ltm_db_path,
            chroma_path=ltm_chroma_dir,
        )
        episodic_conversion = self.episodic_converter(analysis, messages)
        episodic_embedding = episodic_conversion.embedding or embedding

        episodic_ids: list[str] = []
        source_message_ids = [
            str(message["id"])
            for message in messages
            if message.get("id")
        ]
        source_turn_indices = [
            message["turn_index"]
            for message in messages
            if isinstance(message.get("turn_index"), int)
        ]
        message_timestamps = [
            str(message["timestamp"])
            for message in messages
            if message.get("timestamp")
        ]
        last_message_timestamp = max(message_timestamps) if message_timestamps else None
        normalized_topics = normalize_topic_tags(
            episodic_conversion.topic_tags
            or episodic_conversion.topics
            or analysis.topics
            or ["general:conversation"]
        )
        for topic in normalized_topics:
            episodic_ids.append(
                upsert_episodic_record(
                    topic=topic,
                    topic_tags=[topic],
                    strengths=episodic_conversion.strengths,
                    weaknesses=episodic_conversion.weaknesses,
                    questions=episodic_conversion.questions,
                    session_id=session_id,
                    source_message_ids=source_message_ids,
                    source_turn_indices=source_turn_indices,
                    source_message_timestamps=message_timestamps,
                    last_message_timestamp=last_message_timestamp,
                    topic_embedding=episodic_embedding,
                    db_path=episodic_db_path,
                    chroma_dir=episodic_chroma_dir,
                )
            )

        transferred_message_ids = source_message_ids
        deleted_stm_rows = (
            delete_messages_by_ids(self.conn, transferred_message_ids)
            if self.clear_stm
            else 0
        )
        stm_cursor = max(source_turn_indices) if source_turn_indices else -1
        stm_status = "transferred" if self.clear_stm else "transferred_retained"
        now = datetime.now(timezone.utc).isoformat()
        _record_memory_state(
            self.conn,
            session_id=session_id,
            stm_status=stm_status,
            ltm_status="created",
            stm_cursor=stm_cursor,
            last_transferred_turn_index=stm_cursor,
            transferred_message_count=len(transferred_message_ids),
            deleted_stm_rows=deleted_stm_rows,
            ltm_id=ltm_id,
            last_stm_timestamp=last_message_timestamp,
            consolidated_at=now,
        )
        self._processed_session_ids.add(session_id)
        return {
            "session_id": session_id,
            "status": "processed",
            "ltm_id": ltm_id,
            "episodic_ids": episodic_ids,
            "deleted_stm_rows": deleted_stm_rows,
        }


def consolidate_session(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    db_path: str | Path | None = None,
    chroma_dir: str | Path | None = None,
    analyzer: Analyzer = default_analyzer,
    episodic_converter: EpisodicConverter = default_ltm_to_episodic_converter,
    embedding_fn: EmbeddingFn | None = None,
    stm_window: int = 20,
    clear_stm: bool = True,
) -> dict:
    """Convenience entry point for callers that do not need a reusable adapter."""
    return SessionMemoryConsolidator(
        conn,
        db_path=db_path,
        chroma_dir=chroma_dir,
        analyzer=analyzer,
        episodic_converter=episodic_converter,
        embedding_fn=embedding_fn,
        stm_window=stm_window,
        clear_stm=clear_stm,
    ).consolidate(session_id)
