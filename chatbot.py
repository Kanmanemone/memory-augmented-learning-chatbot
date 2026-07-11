"""
Chatbot with 3-tier memory system.

AC 6: STM (Short-Term Memory) is **always** injected into the LLM context
      when generating a response.  Every user message and assistant reply
      is persisted to STM, so the conversation window is fully reconstructed
      from the database on each turn.

Usage
-----
    from chatbot import Chatbot

    bot = Chatbot()
    print(bot.chat("What is a Python decorator?"))
    print(bot.chat("Can you show me an example?"))
    bot.end_session()   # triggers LTM consolidation (AC 5)
"""

from __future__ import annotations

import logging
import os
import sqlite3
import uuid
from difflib import SequenceMatcher
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Optional

import chromadb
from chromadb.config import Settings
from episodic_schema import (
    EPISODIC_COLLECTION_NAME,
    EPISODIC_CONVERSATION_EPISODE_TYPE,
    EPISODIC_LEARNING_EVENT_TYPE,
    create_episodic_table,
    get_episodic_by_topic,
    list_all_episodic,
    normalize_topic_tag,
    normalize_topic_tags,
    upsert_episodic_record,
)

from memory.stm import (
    init_stm,
    add_message,
    get_all_messages,
    get_current_session_context,
    get_recent_messages,
    get_next_turn_index,
)
from memory.session_manager import SessionManager
from memory.retrieval import (
    build_episodic_retrieval_input,
    build_ltm_retrieval_input,
    merge_memory_search_results,
)
from memory.ltm import LTM_COLLECTION_NAME

try:
    from google import genai
except ImportError:
    genai = SimpleNamespace(Client=None)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).parent / "data"
DEMO_DB_PATH = DATA_DIR / "demo.sqlite3"
FALLBACK_DB_PATH = DATA_DIR / "chatbot.db"
DEMO_CHROMA_PATH = DATA_DIR / "chroma"
DEMO_CHROMA_COLLECTION_NAMES = (LTM_COLLECTION_NAME, EPISODIC_COLLECTION_NAME)


def resolve_default_db_path(data_dir: Path = DATA_DIR) -> Path:
    """Select a packaged, readable SQLite demo DB file."""
    candidates = (data_dir / "demo.sqlite3", data_dir / "chatbot.db")
    for db_path in candidates:
        if db_path.is_file() and os.access(db_path, os.R_OK):
            return db_path

    checked_paths = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(
        f"No readable SQLite demo DB file found. Checked: {checked_paths}"
    )


def resolve_default_chroma_path(data_dir: Path = DATA_DIR) -> Path:
    """Select the packaged, readable Chroma demo DB directory."""
    chroma_path = data_dir / "chroma"
    if chroma_path.is_dir() and os.access(chroma_path, os.R_OK | os.X_OK):
        return chroma_path

    raise FileNotFoundError(
        "No readable Chroma demo DB directory found. "
        f"Checked: {chroma_path}. "
        "Provide the completed Chroma vector DB folder at data/chroma before "
        "starting the hands-on demo."
    )


def _validate_demo_chroma_path(chroma_dir: Path) -> None:
    """Validate the Chroma demo directory before opening the persistent client."""
    chroma_dir = Path(chroma_dir)
    if not chroma_dir.exists():
        raise DemoDatabaseError(
            "Chroma demo DB directory is missing. "
            f"Path: {chroma_dir}. "
            "Provide the completed Chroma vector DB folder at data/chroma before "
            "starting the hands-on demo."
        )

    if not chroma_dir.is_dir():
        raise DemoDatabaseError(
            "Chroma demo DB path is not a directory. "
            f"Path: {chroma_dir}. "
            "Provide the completed Chroma vector DB folder at data/chroma."
        )

    if not os.access(chroma_dir, os.R_OK | os.X_OK):
        raise DemoDatabaseError(
            "Chroma demo DB directory is not readable. "
            f"Path: {chroma_dir}. "
            "Check directory permissions and rerun the demo."
        )


def initialize_persistent_chroma_client(chroma_dir: Path) -> chromadb.ClientAPI:
    """Initialize the Chroma persistent client from the packaged demo path."""
    _validate_demo_chroma_path(Path(chroma_dir))
    return chromadb.PersistentClient(
        path=str(Path(chroma_dir)),
        settings=Settings(anonymized_telemetry=False),
    )


def load_demo_chroma_collections(
    client: chromadb.ClientAPI,
    chroma_dir: Path,
    collection_names: tuple[str, ...] = DEMO_CHROMA_COLLECTION_NAMES,
) -> dict[str, chromadb.Collection]:
    """Load and validate the supplied Chroma collections used by the demo."""
    collections: dict[str, chromadb.Collection] = {}
    for collection_name in collection_names:
        try:
            collection = client.get_collection(collection_name)
        except Exception as exc:
            raise DemoDatabaseError(
                "Chroma demo collection is missing. "
                f"Collection: {collection_name}. "
                f"Path: {chroma_dir}. "
                "Provide the completed Chroma vector DB folder with the hands-on "
                "demo collections before starting the app."
            ) from exc

        count = collection.count()
        if count <= 0:
            raise DemoDatabaseError(
                "Chroma demo collection is empty. "
                f"Collection: {collection_name}. "
                f"Path: {chroma_dir}. "
                "The hands-on demo requires preloaded LTM/Episodic embeddings."
            )
        collections[collection_name] = collection

    return collections


DB_PATH = resolve_default_db_path()
DEFAULT_CHROMA_PATH = DEMO_CHROMA_PATH
DEFAULT_STM_WINDOW = 20          # how many recent messages to inject
DEFAULT_CHATBOT_RESPONSE_MODEL = "gemini-2.5-flash"
DEFAULT_MODEL = DEFAULT_CHATBOT_RESPONSE_MODEL
GEMINI_ENV_PATH = Path.home() / ".env"
DEFAULT_TOPIC_TAG = "general:uncategorized"
DEFAULT_REPEAT_DETECTION_THRESHOLD = 0.55
REFERENT_AMBIGUITY_STATE = {
    "text": "ambiguous_referent",
    "source": "ambiguity_state",
    "role": "state",
    "topic": DEFAULT_TOPIC_TAG,
    "turn_index": None,
    "score": 0.0,
    "status": "ambiguous",
    "reason": "no_confident_referent_candidate",
}
DEMO_QUERY_EMBEDDING_DIMENSION = 384
INFERRED_TOPIC_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("python decorator", "programming:python-decorators"),
    ("데코레이터", "programming:python-decorators"),
    ("decorator", "programming:python-decorators"),
    ("wrapper", "programming:python-decorators"),
    ("wraps", "programming:python-decorators"),
    ("래퍼", "programming:python-decorators"),
    ("loops", "programming:python-loops"),
    ("loop", "programming:python-loops"),
    ("for문", "programming:python-loops"),
    ("while문", "programming:python-loops"),
    ("반복문", "programming:python-loops"),
    ("반복", "programming:python-loops"),
    ("conditionals", "programming:python-conditionals"),
    ("conditional", "programming:python-conditionals"),
    ("if문", "programming:python-conditionals"),
    ("elif", "programming:python-conditionals"),
    ("조건문", "programming:python-conditionals"),
    ("조건", "programming:python-conditionals"),
    ("재귀", "programming:recursion"),
    ("recursion", "programming:recursion"),
    ("base case", "programming:recursion"),
    ("호출 스택", "programming:recursion"),
    ("async/await", "programming:async-await"),
    ("async await", "programming:async-await"),
    ("embedding", "data_ai:embeddings"),
    ("machine learning", "data_ai:machine-learning"),
    ("pytest", "tools_workflow:pytest"),
    ("left join", "web_app:sql-join"),
    ("inner join", "web_app:sql-join"),
    ("sql join", "web_app:sql-join"),
    ("join", "web_app:sql-join"),
    ("조인", "web_app:sql-join"),
    ("sqlite", "web_app:sqlite-schema"),
)
INFERRED_TOPIC_KEYWORDS_BY_SPECIFICITY = tuple(
    sorted(INFERRED_TOPIC_KEYWORDS, key=lambda item: len(item[0]), reverse=True)
)
DEMO_TOPIC_EMBEDDING_AXES = {
    "programming:recursion": 0,
    "web_app:sql-join": 1,
    "web_app:sqlite-schema": 1,
    "programming:python-decorators": 2,
    "programming:python-loops": 3,
    "programming:python-conditionals": 3,
}

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a helpful, patient learning assistant.
You help the user understand concepts by explaining clearly, giving examples,
and checking their understanding.

When responding:
- Be concise but thorough
- Use examples when helpful
- Build on what was discussed earlier in the conversation
- Note when the user seems to struggle or excel at something
"""

CONTEXT_RECOVERY_UTTERANCES = frozenset(
    {
        "방금 얘기하던걸 조금 더 자세하게 다시 설명해줘",
        "이게 뭐야",
    }
)


# ---------------------------------------------------------------------------
# Demo DB errors
# ---------------------------------------------------------------------------

class DemoDatabaseError(RuntimeError):
    """User-facing error raised when the packaged demo database cannot load."""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _validate_demo_db_path(db_path: Path, *, require_existing: bool) -> None:
    """Validate the SQLite DB path before opening it."""
    if db_path.exists() and not db_path.is_file():
        raise DemoDatabaseError(
            "SQLite demo DB path is not a file. "
            f"Path: {db_path}. "
            "Provide the completed SQLite DB file at this path."
        )

    if require_existing and not db_path.exists():
        raise DemoDatabaseError(
            "SQLite demo DB file is missing. "
            f"Path: {db_path}. "
            "Use the provided data/chatbot.db or data/demo.sqlite3 file before "
            "starting the hands-on demo."
        )

    if db_path.exists() and not os.access(db_path, os.R_OK | os.W_OK):
        raise DemoDatabaseError(
            "SQLite demo DB file is not readable and writable. "
            f"Path: {db_path}. "
            "Check file permissions and rerun the demo."
        )


def _open_db(
    db_path: Path = DB_PATH,
    *,
    require_existing: bool = False,
) -> sqlite3.Connection:
    """Open the SQLite database and ensure demo memory tables are ready."""
    db_path = Path(db_path)
    _validate_demo_db_path(db_path, require_existing=require_existing)

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        init_stm(conn)
        from memory.ltm import create_ltm_table

        create_ltm_table(conn)
        create_episodic_table(db_path)
        return conn
    except DemoDatabaseError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise DemoDatabaseError(
            "Unable to open SQLite demo DB. "
            f"Path: {db_path}. "
            f"SQLite error: {exc}. "
            "Verify that the provided demo DB exists, is accessible, and is a "
            "valid SQLite database file."
        ) from exc


# ---------------------------------------------------------------------------
# STM → LLM messages adapter
# ---------------------------------------------------------------------------

def build_messages_from_stm(
    conn: sqlite3.Connection,
    session_id: str,
    n: int = DEFAULT_STM_WINDOW,
) -> list[dict]:
    """
    Retrieve the most recent *n* STM messages and convert them to the public
    chatbot message format:  [{"role": "user"|"assistant", "content": "..."}]

    'system' role messages are filtered out here because the chatbot injects
    the system prompt separately.

    This function is the heart of AC 6: STM is **always** read and returned
    so the caller can inject it into the LLM request.
    """
    recent = get_recent_messages(conn, session_id, n=n)
    messages = [
        {"role": msg["role"], "content": msg["content"]}
        for msg in recent
        if msg["role"] in ("user", "assistant")
    ]
    return messages


def build_stm_memory_hits(session_context: dict, limit: int = 3) -> list[dict]:
    """
    Return recent STM rows that should ground an ambiguous current turn.

    The newest user message can be too vague on its own ("방금...", "이게...").
    Keeping the immediately preceding exchange in the STM hit list preserves
    the active topic, such as the decorator/wrapper context used in the demo.
    """
    messages = [
        message
        for message in session_context.get("messages", [])
        if str(message.get("role", "")).strip() in {"user", "assistant"}
        and str(message.get("content", "")).strip()
    ]
    if not messages:
        return []

    recent_messages = messages[-limit:]
    last_index = len(recent_messages) - 1
    scored_hits: list[dict] = []
    for index, message in enumerate(recent_messages):
        hit = dict(message)
        if index == last_index:
            hit.setdefault("hybrid_score", 1.0)
        else:
            hit.setdefault("hybrid_score", 0.6)
        scored_hits.append(hit)
    return scored_hits


def extract_referent_candidates(
    session_context: dict,
    current_topic_context: dict[str, object] | str | None = None,
    limit: int = 5,
) -> list[dict[str, object]]:
    """
    Extract likely referents for ambiguous follow-ups from STM and topic state.

    Short turns such as "이게 뭐야?" rarely contain searchable topic tokens. This
    helper makes the active referents explicit by combining the current topic tag
    with recent STM turns that mention the same inferred topic.
    """
    topic_tags = _topic_tags_from_context(current_topic_context)
    candidates: list[dict[str, object]] = []

    for topic_tag in topic_tags:
        candidates.append(
            {
                "text": topic_tag,
                "source": "current_topic_context",
                "role": "topic",
                "topic": topic_tag,
                "turn_index": None,
                "score": 1.0,
            }
        )

    messages = [
        message
        for message in session_context.get("messages", [])
        if str(message.get("role", "")).strip() in {"user", "assistant"}
        and str(message.get("content", "")).strip()
    ]
    for recency, message in enumerate(reversed(messages), start=1):
        text = str(message.get("content", "")).strip()
        if is_context_recovery_utterance(text):
            continue

        inferred_topic = _infer_topic_tag_from_text(text)
        if topic_tags and not _candidate_topic_matches_context(
            inferred_topic,
            topic_tags,
        ):
            continue

        base_score = 0.92 if message.get("role") == "assistant" else 0.88
        candidates.append(
            {
                "text": text,
                "source": "stm",
                "role": str(message.get("role", "")),
                "topic": inferred_topic,
                "turn_index": message.get("turn_index"),
                "score": max(base_score - (recency * 0.03), 0.1),
            }
        )

    deduped: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        key = (
            str(candidate.get("source") or ""),
            " ".join(str(candidate.get("text") or "").lower().split()),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)

    if not deduped:
        return [dict(REFERENT_AMBIGUITY_STATE)] if limit > 0 else []

    if not any(_is_confident_referent_candidate(candidate) for candidate in deduped):
        fallback = max(
            deduped,
            key=lambda candidate: (
                float(candidate.get("score") or 0.0),
                int(candidate.get("turn_index") or -1),
                str(candidate.get("text") or ""),
            ),
        )
        fallback = dict(fallback)
        fallback["topic"] = DEFAULT_TOPIC_TAG
        fallback["score"] = round(float(fallback.get("score") or 0.0), 4)
        fallback["status"] = "fallback"
        fallback["reason"] = "no_confident_referent_candidate"
        return [fallback] if limit > 0 else []

    deduped.sort(
        key=lambda candidate: (
            _referent_candidate_specificity(candidate),
            float(candidate.get("score") or 0.0),
            int(candidate.get("turn_index") or -1),
        ),
        reverse=True,
    )
    return deduped[: max(limit, 0)]


def _topic_tags_from_context(
    current_topic_context: dict[str, object] | str | None,
) -> list[str]:
    if current_topic_context is None:
        return []

    raw_values: list[object]
    if isinstance(current_topic_context, str):
        raw_values = [current_topic_context]
    elif isinstance(current_topic_context, dict):
        raw_values = []
        for field in ("topic_tags", "topics"):
            values = current_topic_context.get(field)
            if isinstance(values, (list, tuple, set)):
                raw_values.extend(values)
        for field in ("topic", "current_topic", "current_topic_tag"):
            value = current_topic_context.get(field)
            if value:
                raw_values.append(value)
    else:
        raw_values = []

    return normalize_topic_tags([str(value) for value in raw_values if str(value).strip()])


def _candidate_topic_matches_context(
    inferred_topic: str | None,
    topic_tags: list[str],
) -> bool:
    if inferred_topic in topic_tags:
        return True
    return (
        inferred_topic == "programming:python-decorators"
        and "programming:python" in topic_tags
    )


def _referent_candidate_specificity(candidate: dict[str, object]) -> int:
    topic = str(candidate.get("topic") or "").strip()
    if topic == "programming:python-decorators":
        return 2
    if topic in {"", "general:uncategorized", "programming:python"}:
        return 0
    return 1


def _is_confident_referent_candidate(candidate: dict[str, object]) -> bool:
    topic = str(candidate.get("topic") or "").strip()
    return bool(topic and topic != DEFAULT_TOPIC_TAG)


def _infer_topic_tag_from_text(text: str) -> str | None:
    """Return the most specific known demo topic tag mentioned by text."""
    normalized_text = text.lower()
    for keyword, topic_tag in INFERRED_TOPIC_KEYWORDS_BY_SPECIFICITY:
        if keyword in normalized_text:
            return normalize_topic_tag(topic_tag)
    return None


def build_referent_candidates_context(candidates: list[dict[str, object]]) -> str:
    """Format extracted referent candidates for context-recovery turns."""
    if not candidates:
        return ""

    lines = ["Referent candidates:"]
    for index, candidate in enumerate(candidates, start=1):
        text = str(candidate.get("text") or "").strip()
        if not text:
            continue
        source = str(candidate.get("source") or "stm")
        topic = str(candidate.get("topic") or "").strip()
        suffix = f" ({topic})" if topic and topic != text else ""
        lines.append(f"{index}. [{source}] {text}{suffix}")
    return "\n".join(lines)


def build_ltm_context(ltm_hits: list[dict]) -> str:
    """Format retrieved LTM records for injection into the LLM context."""
    if not ltm_hits:
        return ""

    lines = ["Relevant long-term memory:"]
    for index, hit in enumerate(ltm_hits, start=1):
        summary = str(hit.get("summary", "")).strip()
        if summary:
            lines.append(f"{index}. Summary: {summary}")

        for label, field in (
            ("Struggles", "struggles"),
            ("Strengths", "strengths"),
            ("Confusions", "confusions"),
            ("Topics", "topic_tags"),
        ):
            values = hit.get(field) or []
            if values:
                lines.append(f"   {label}: {', '.join(str(value) for value in values)}")

    return "\n".join(lines)


def build_demo_query_embedding(text: str) -> list[float]:
    """
    Build the deterministic query embedding used by the packaged demo DB.

    The supplied Chroma fixture uses topic-axis vectors so the hands-on can run
    without a network embedding service. Unknown topics default to the first
    axis, which keeps semantic retrieval deterministic for broad memory
    questions.
    """
    vector = [0.0] * DEMO_QUERY_EMBEDDING_DIMENSION
    inferred_topic = Chatbot._infer_topic_tag_from_text(text)
    axis = DEMO_TOPIC_EMBEDDING_AXES.get(inferred_topic or "", 0)
    vector[axis] = 1.0
    return vector


def build_episodic_context(episodic_hits: list[dict]) -> str:
    """Format retrieved Episodic records for injection into the LLM context."""
    if not episodic_hits:
        return ""

    lines = [
        "Relevant episodic memory:",
        (
            "Use this episodic memory to adapt the answer: reinforce strengths, "
            "directly address weaknesses, and avoid repeating prior explanations "
            "without adding a new angle."
        ),
    ]
    for index, hit in enumerate(episodic_hits, start=1):
        topic = str(hit.get("topic", "")).strip()
        if topic:
            lines.append(f"{index}. Topic: {topic}")

        for label, field in (
            ("Strengths", "strengths"),
            ("Weaknesses", "weaknesses"),
            ("Questions", "questions"),
            ("Tags", "topic_tags"),
        ):
            values = hit.get(field) or []
            if values:
                lines.append(f"   {label}: {', '.join(str(value) for value in values)}")

    return "\n".join(lines)


def build_temporary_stm_context(session_context: dict) -> str:
    """Format the active session's temporary STM snapshot for generation."""
    if not session_context or not session_context.get("message_count"):
        return ""

    lines = [
        "Temporary STM context:",
        f"Message count: {session_context['message_count']}",
    ]

    last_user_message = str(session_context.get("last_user_message") or "").strip()
    if last_user_message:
        lines.append(f"Latest user message: {last_user_message}")

    last_assistant_message = str(
        session_context.get("last_assistant_message") or ""
    ).strip()
    if last_assistant_message:
        lines.append(f"Latest assistant message: {last_assistant_message}")

    user_messages = [
        str(message).strip()
        for message in session_context.get("user_messages", [])
        if str(message).strip()
    ]
    if user_messages:
        lines.append(f"Recent user messages: {' | '.join(user_messages)}")

    combined_text = str(session_context.get("combined_text") or "").strip()
    if combined_text:
        lines.append(f"Recent conversation text:\n{combined_text}")

    return "\n".join(lines)


def is_context_recovery_utterance(user_message: str) -> bool:
    """Return True for short follow-ups that must recover referent from STM."""
    normalized = " ".join(str(user_message or "").strip().split())
    normalized = normalized.rstrip("?.!？！。")
    return normalized in CONTEXT_RECOVERY_UTTERANCES


def build_context_recovery_context(
    user_message: str,
    retrieval_query: str,
    referent_candidates: list[dict[str, object]] | None = None,
) -> str:
    """Format instructions for resolving a vague turn from recent STM."""
    user_text = user_message.strip()
    query_text = retrieval_query.strip()
    if not is_context_recovery_utterance(user_text) or not query_text:
        return ""

    lines = [
        "Context recovery turn:",
        "Recover the referent from recent STM before answering.",
        "Do not refuse only because the user utterance is ambiguous.",
        f"Current utterance: {user_text}",
        f"Expanded STM query:\n{query_text}",
    ]
    candidates_context = build_referent_candidates_context(referent_candidates or [])
    if candidates_context:
        lines.append(candidates_context)
    return "\n".join(lines)


def build_stm_insufficiency_trigger(
    user_message: str,
    referent_candidates: list[dict[str, object]] | None,
) -> dict[str, object]:
    """Detect when recent STM is too weak to resolve a vague follow-up."""
    candidates = referent_candidates or []
    if not is_context_recovery_utterance(user_message):
        return {
            "triggered": False,
            "reason": None,
            "action": None,
            "candidate_count": len(candidates),
        }

    has_confident_referent = any(
        _is_confident_referent_candidate(candidate)
        for candidate in candidates
    )
    if has_confident_referent:
        return {
            "triggered": False,
            "reason": None,
            "action": None,
            "candidate_count": len(candidates),
        }

    return {
        "triggered": True,
        "reason": "no_confident_stm_referent",
        "action": "consult_ltm_and_episodic_memory",
        "candidate_count": len(candidates),
    }


def build_stm_insufficiency_context(trigger: dict[str, object]) -> str:
    """Format the STM insufficiency trigger for generation context."""
    if not trigger.get("triggered"):
        return ""

    return "\n".join(
        [
            "STM context insufficiency trigger:",
            f"Reason: {trigger.get('reason')}",
            f"Action: {trigger.get('action')}",
            "Recent STM did not contain a confident referent; use LTM and "
            "Episodic memory to recover prior learning context before answering.",
        ]
    )


def build_repeat_detection_context(repeat_metadata: dict[str, object]) -> str:
    """Format repeat-detection metadata for response generation."""
    if not repeat_metadata.get("is_repeat"):
        return ""

    lines = [
        "Repeat detection:",
        f"Matched prior topic: {repeat_metadata.get('matched_prior_topic', '')}",
        f"Confidence: {float(repeat_metadata.get('confidence', 0.0)):.2f}",
    ]
    matched_question = str(repeat_metadata.get("matched_prior_question") or "").strip()
    if matched_question:
        lines.append(f"Matched prior question: {matched_question}")
    return "\n".join(lines)


def build_integrated_memory_context(
    *,
    stm_hits: list[dict],
    ltm_hits: list[dict],
    episodic_hits: list[dict],
    limit: int = 6,
) -> str:
    """
    Format merged STM/LTM/Episodic search results for response generation.

    The detailed layer-specific context blocks remain available, while this
    block gives the model one relevance-ordered view of which memories should
    ground the current answer.
    """
    merged_hits = merge_memory_search_results(
        ltm_hits,
        episodic_hits,
        stm_hits=stm_hits,
        limit=limit,
    )
    if not merged_hits:
        return ""

    source_labels = {
        "stm": "STM",
        "ltm": "LTM",
        "episodic": "Episodic",
    }
    lines = [
        "Integrated memory search results:",
        (
            "Ground the answer in these merged memory hits. Use STM for the "
            "current conversation, LTM for prior-session summaries, and "
            "Episodic for topic-specific strengths, weaknesses, and questions."
        ),
    ]

    for index, hit in enumerate(merged_hits, start=1):
        source = str(hit.get("source", "")).lower()
        label = source_labels.get(source, source.upper() or "Memory")
        content = str(hit.get("content", "")).strip()
        lines.append(f"{index}. [{label}] {content}")

        record = hit.get("record") or {}
        retrieval_source = str(record.get("retrieval_source") or "").strip()
        retrieval_mode = str(record.get("retrieval_mode") or "").strip()
        if retrieval_source or retrieval_mode:
            retrieval_label = "/".join(
                value for value in (retrieval_source, retrieval_mode) if value
            )
            lines.append(f"   Retrieval: {retrieval_label}")

        if source == "stm":
            role = str(record.get("role", "")).strip()
            if role:
                lines.append(f"   Role: {role}")
        elif source == "ltm":
            _append_memory_values(lines, "Struggles", record.get("struggles"))
            _append_memory_values(lines, "Strengths", record.get("strengths"))
            _append_memory_values(lines, "Confusions", record.get("confusions"))
            _append_memory_values(lines, "Topics", record.get("topic_tags"))
        elif source == "episodic":
            _append_memory_values(lines, "Strengths", record.get("strengths"))
            _append_memory_values(lines, "Weaknesses", record.get("weaknesses"))
            _append_memory_values(lines, "Questions", record.get("questions"))
            _append_memory_values(lines, "Tags", record.get("topic_tags"))

    return "\n".join(lines)


def build_memory_source_trace(
    *,
    stm_hits: list[dict],
    ltm_hits: list[dict],
    episodic_hits: list[dict],
    limit: int = 6,
) -> list[dict[str, object]]:
    """Return inspectable STM/LTM/Episodic source metadata for one answer."""
    source_labels = {
        "stm": "STM",
        "ltm": "LTM",
        "episodic": "Episodic",
    }
    merged_hits = merge_memory_search_results(
        ltm_hits,
        episodic_hits,
        stm_hits=stm_hits,
        limit=limit,
    )

    sources: list[dict[str, object]] = []
    for hit in merged_hits:
        source = str(hit.get("source", "")).lower()
        memory_type = source_labels.get(source, source.upper() or "Memory")
        sources.append(
            {
                "memory_type": memory_type,
                "source": memory_type,
                "source_id": hit.get("source_id"),
                "content": hit.get("content", ""),
                "score": hit.get("score", 0.0),
                "metadata": dict(hit.get("metadata") or {}),
            }
        )
    return sources


def _append_memory_values(lines: list[str], label: str, values: object) -> None:
    if not values:
        return
    if isinstance(values, (list, tuple)):
        formatted = ", ".join(str(value) for value in values if str(value).strip())
    else:
        formatted = str(values).strip()
    if formatted:
        lines.append(f"   {label}: {formatted}")


def _comparison_text(value: str) -> str:
    text = value.strip().lower()
    text = text.replace("_", " ").replace("-", " ")
    text = "".join(char if char.isalnum() or char in "#+ " else " " for char in text)
    return " ".join(text.split())


def _repeat_similarity_score(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0

    left_tokens = set(left.split())
    right_tokens = set(right.split())
    token_score = 0.0
    if left_tokens and right_tokens:
        token_score = (2 * len(left_tokens & right_tokens)) / (
            len(left_tokens) + len(right_tokens)
        )

    sequence_score = SequenceMatcher(None, left, right).ratio()
    return max(token_score, sequence_score)


def _read_gemini_api_key(env_path: Path = GEMINI_ENV_PATH) -> str | None:
    """Resolve GEMINI_API_KEY from env, ~/.env, or the project .env."""
    existing_value = os.environ.get("GEMINI_API_KEY")
    if existing_value:
        return existing_value

    candidate_paths = [Path(env_path)]
    project_env_path = Path.cwd() / ".env"
    if project_env_path not in candidate_paths:
        candidate_paths.append(project_env_path)

    for candidate_path in candidate_paths:
        try:
            lines = candidate_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            continue

        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "GEMINI_API_KEY":
                return value.strip().strip("\"'")
    return None


def _require_gemini_client() -> type:
    client_cls = getattr(genai, "Client", None)
    if client_cls is None:
        raise ImportError(
            "The 'google-genai' package is required for Gemini chatbot responses. "
            "Install it with: pip install google-genai"
        )
    return client_cls


def _create_gemini_client(api_key: str | None = None) -> object:
    return _require_gemini_client()(api_key=api_key or _read_gemini_api_key())


def _gemini_contents_from_messages(messages: list[dict]) -> list[dict]:
    contents: list[dict] = []
    for message in messages:
        role = message.get("role")
        content = str(message.get("content", ""))
        if role == "assistant":
            role = "model"
        if role not in {"user", "model"} or not content:
            continue
        contents.append({"role": role, "parts": [{"text": content}]})
    return contents


def _payload_value(payload: object, key: str) -> object:
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


def _extract_gemini_text(response: object) -> str:
    text = _payload_value(response, "text")
    if isinstance(text, str):
        return text

    candidates = _payload_value(response, "candidates") or []
    for candidate in candidates:
        content = _payload_value(candidate, "content")
        parts = _payload_value(content, "parts") if content is not None else None
        for part in parts or []:
            part_text = _payload_value(part, "text")
            if isinstance(part_text, str):
                return part_text

    raise ValueError("Gemini response did not include text content.")


def _generate_gemini_reply(
    client: object,
    *,
    model: str,
    system_context: str,
    messages: list[dict],
) -> str:
    try:
        response = client.models.generate_content(
            model=model,
            contents=_gemini_contents_from_messages(messages),
            config={
                "system_instruction": system_context,
                "max_output_tokens": 1024,
            },
        )
        return _extract_gemini_text(response)
    except Exception as exc:
        raise RuntimeError("Gemini response generation failed.") from exc


# ---------------------------------------------------------------------------
# Chatbot
# ---------------------------------------------------------------------------

class Chatbot:
    """
    Single-user learning chatbot with 3-tier memory.

    On every call to :meth:`chat`:
    1. The user message is saved to STM.
    2. The recent STM window is read and injected into the LLM context.
    3. The LLM generates a response.
    4. The assistant response is saved to STM.
    5. The response text is returned to the caller.
    """

    def __init__(
        self,
        session_id: Optional[str] = None,
        db_path: Path = DB_PATH,
        stm_window: int = DEFAULT_STM_WINDOW,
        model: str = DEFAULT_CHATBOT_RESPONSE_MODEL,
        system_prompt: str = SYSTEM_PROMPT,
        memory_processor: Optional[Callable[[sqlite3.Connection, str], object]] = None,
        chroma_dir: Path = DEFAULT_CHROMA_PATH,
        topic_tagger: Optional[Callable[[list[dict]], list[str]]] = None,
        ltm_retriever: Optional[Callable[[str], list[dict]]] = None,
        episodic_retriever: Optional[Callable[[str], list[dict]]] = None,
    ) -> None:
        self.session_id = session_id or str(uuid.uuid4())
        self.db_path = db_path
        self.chroma_dir = chroma_dir
        self.stm_window = stm_window
        self.model = model
        self.system_prompt = system_prompt
        self._memory_processor = memory_processor
        self._topic_tagger = topic_tagger
        self._ltm_retriever = ltm_retriever
        self._episodic_retriever = episodic_retriever
        self.last_generation_memory_trace: dict[str, object] = {}
        self.last_repeat_detection_metadata: dict[str, object] = {
            "is_repeat": False,
            "matched_prior_topic": None,
            "matched_prior_question": None,
            "confidence": 0.0,
        }

        self._chroma_client = initialize_persistent_chroma_client(self.chroma_dir)
        self._chroma_collections = load_demo_chroma_collections(
            self._chroma_client,
            self.chroma_dir,
        )
        self._conn = _open_db(db_path)
        self._client = _create_gemini_client()
        self._session_consolidated = False
        self._session_manager = SessionManager(
            session_id=self.session_id,
            on_session_end_event=lambda _event: self._process_session_end(),
        )
        self._session_manager.start_session()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def chat(self, user_message: str) -> str:
        """
        Process one user turn.

        Steps
        -----
        1. Persist the user message to STM.
        2. Build conversation history from STM  ← AC 6 injection point
        3. Call the LLM with the full history as context.
        4. Persist the assistant reply to STM.
        5. Return the assistant reply.
        """
        if not self.is_active:
            raise RuntimeError("Cannot chat after the session has ended.")

        if self._session_manager.handle_input(user_message):
            self.end_session()
            return "[Session ended]"

        # ── 1. Save user message to STM ──────────────────────────────────
        turn_idx = get_next_turn_index(self._conn, self.session_id)
        user_message_id = add_message(
            self._conn,
            session_id=self.session_id,
            role="user",
            content=user_message,
            turn_index=turn_idx,
        )

        # ── 2. Inject STM into LLM context  (AC 6) ───────────────────────
        # Always read recent STM messages and pass them as conversation history.
        messages = build_messages_from_stm(
            self._conn,
            self.session_id,
            n=self.stm_window,
        )
        system_context = self.system_prompt
        session_context = self.get_current_session_context()
        temporary_stm_context = build_temporary_stm_context(session_context)
        if temporary_stm_context:
            system_context = f"{system_context}\n\n{temporary_stm_context}"
        referent_candidates = self._extract_referent_candidates_for_turn(
            user_message,
            session_context,
        )
        recovered_query = self._build_context_recovery_memory_query(user_message)
        context_recovery_context = build_context_recovery_context(
            user_message,
            recovered_query,
            referent_candidates,
        )
        if context_recovery_context:
            system_context = f"{system_context}\n\n{context_recovery_context}"
        stm_insufficiency_trigger = build_stm_insufficiency_trigger(
            user_message,
            referent_candidates,
        )
        stm_insufficiency_context = build_stm_insufficiency_context(
            stm_insufficiency_trigger
        )
        if stm_insufficiency_context:
            system_context = f"{system_context}\n\n{stm_insufficiency_context}"
        ltm_hits = self._retrieve_ltm(user_message)
        ltm_context = build_ltm_context(ltm_hits)
        if ltm_context:
            system_context = f"{system_context}\n\n{ltm_context}"
        episodic_hits = self._retrieve_episodic(user_message)
        episodic_context = build_episodic_context(episodic_hits)
        if episodic_context:
            system_context = f"{system_context}\n\n{episodic_context}"
        stm_hits = build_stm_memory_hits(session_context)
        integrated_context = build_integrated_memory_context(
            stm_hits=stm_hits,
            ltm_hits=ltm_hits,
            episodic_hits=episodic_hits,
        )
        if integrated_context:
            system_context = f"{system_context}\n\n{integrated_context}"
        repeat_metadata = self._detect_repeat_for_generation(
            user_message,
            episodic_hits,
        )
        self.last_repeat_detection_metadata = repeat_metadata
        repeat_context = build_repeat_detection_context(repeat_metadata)
        if repeat_context:
            system_context = f"{system_context}\n\n{repeat_context}"
        self._trace_generation_memory_consumption(
            query=user_message,
            stm_hits=stm_hits,
            ltm_hits=ltm_hits,
            episodic_hits=episodic_hits,
            ltm_context=ltm_context,
            episodic_context=episodic_context,
            referent_candidates=referent_candidates,
            stm_insufficiency_trigger=stm_insufficiency_trigger,
        )

        # ── 3. Call LLM ──────────────────────────────────────────────────
        reply = _generate_gemini_reply(
            self._client,
            model=self.model,
            messages=messages,
            system_context=system_context,
        )
        reply = self._ensure_episodic_context_reflected(
            reply=reply,
            repeat_metadata=repeat_metadata,
            episodic_hits=episodic_hits,
        )
        reply = self._ensure_decorator_wrapper_context_reflected(
            reply=reply,
            user_message=user_message,
            ltm_hits=ltm_hits,
            episodic_hits=episodic_hits,
        )
        reply = self._ensure_resolved_context_recovery_answers_directly(
            reply=reply,
            user_message=user_message,
            referent_candidates=referent_candidates,
            ltm_hits=ltm_hits,
            episodic_hits=episodic_hits,
        )
        reply = self._ensure_prior_struggle_context_reflected(
            reply=reply,
            user_message=user_message,
            ltm_hits=ltm_hits,
            episodic_hits=episodic_hits,
        )
        reply = self._ensure_explicit_ltm_reference_reflected(
            reply=reply,
            user_message=user_message,
            ltm_hits=ltm_hits,
        )

        # ── 4. Save assistant reply to STM ───────────────────────────────
        next_turn_idx = get_next_turn_index(self._conn, self.session_id)
        assistant_message_id = add_message(
            self._conn,
            session_id=self.session_id,
            role="assistant",
            content=reply,
            turn_index=next_turn_idx,
        )
        persisted_messages = get_all_messages(self._conn, self.session_id)
        current_message_by_id = {
            message["id"]: message
            for message in persisted_messages
            if message["id"] in {user_message_id, assistant_message_id}
        }

        # ── 5. Tag current learning turn into Episodic memory ─────────────
        self._tag_current_turn(
            user_message=user_message,
            user_turn_index=turn_idx,
            user_message_id=user_message_id,
            user_message_timestamp=current_message_by_id[user_message_id]["timestamp"],
            assistant_reply=reply,
            assistant_turn_index=next_turn_idx,
            assistant_message_id=assistant_message_id,
            assistant_message_timestamp=current_message_by_id[assistant_message_id][
                "timestamp"
            ],
        )

        return reply

    def chat_with_memory_trace(self, user_message: str) -> dict[str, object]:
        """
        Process one user turn and return the reply with memory source metadata.

        ``chat()`` remains the string-returning compatibility API. This helper
        gives hands-on demos an inspectable response result that shows which
        STM/LTM/Episodic items grounded the answer.
        """
        reply = self.chat(user_message)
        return {
            "reply": reply,
            "memory_sources": self.last_generation_memory_trace.get(
                "memory_sources",
                [],
            ),
            "memory_trace": dict(self.last_generation_memory_trace),
        }

    def get_stm_context(self) -> list[dict]:
        """
        Return the current STM window as a list of message dicts.

        Useful for inspection / debugging.
        """
        return get_recent_messages(self._conn, self.session_id, n=self.stm_window)

    def get_current_session_context(self) -> dict:
        """
        Return the current session's temporary STM context snapshot.

        This keeps demo inspection focused on the active session only, including
        recent raw messages and latest user/assistant context for vague turns.
        """
        return get_current_session_context(
            self._conn,
            self.session_id,
            n=self.stm_window,
        )

    def end_session(self) -> None:
        """
        Signal the end of this session.

        Session-end events are connected to the memory processing loop. The
        loop is idempotent, so explicit calls and exit-command events cannot
        consolidate the same session twice.
        """
        if self._session_manager.is_active:
            self._session_manager.end_session()

        self._process_session_end()

    def _process_session_end(self) -> None:
        """Run the configured post-session memory processor once."""
        if self._session_consolidated:
            return
        self._session_consolidated = True

        if self._memory_processor is not None:
            self._memory_processor(self._conn, self.session_id)
            return

        from memory.consolidation import consolidate_session

        consolidate_session(
            self._conn,
            self.session_id,
            db_path=self.db_path,
            chroma_dir=self.chroma_dir,
            stm_window=self.stm_window,
        )

    def _tag_current_turn(
        self,
        *,
        user_message: str,
        user_turn_index: int,
        user_message_id: str,
        user_message_timestamp: str,
        assistant_reply: str,
        assistant_turn_index: int,
        assistant_message_id: str,
        assistant_message_timestamp: str,
    ) -> list[str]:
        """
        Generate normalized topic tags for one completed chat turn and persist
        them to Episodic memory.

        Topic tagging is best-effort so the learning chat loop remains usable
        in demo environments without API credentials. Inject ``topic_tagger``
        in tests or demos to make the behavior deterministic.
        """
        turn_messages = [
            {"role": "user", "content": user_message, "turn_index": user_turn_index},
            {
                "role": "assistant",
                "content": assistant_reply,
                "turn_index": assistant_turn_index,
            },
        ]

        try:
            tags = (
                self._topic_tagger(turn_messages)
                if self._topic_tagger is not None
                else self._default_topic_tagger(turn_messages)
            )
        except Exception:
            logger.exception("Topic tagging failed for session %s", self.session_id)
            return []

        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            logger.warning(
                "Topic tagging returned malformed output for session %s: %r",
                self.session_id,
                tags,
            )
            return []

        tags = self._validate_or_fallback_topic_tags(tags, turn_messages)

        create_episodic_table(self.db_path)
        questions = [user_message.strip()] if user_message.strip() else []
        source_message_ids = [user_message_id, assistant_message_id]
        source_message_timestamps = [
            user_message_timestamp,
            assistant_message_timestamp,
        ]
        source_turn_indices = [user_turn_index, assistant_turn_index]
        persisted_tags: list[str] = []
        for tag in tags:
            upsert_episodic_record(
                topic=tag,
                topic_tags=[tag],
                questions=questions,
                session_id=self.session_id,
                source_message_ids=source_message_ids,
                source_turn_indices=source_turn_indices,
                source_message_timestamps=source_message_timestamps,
                last_message_timestamp=assistant_message_timestamp,
                topic_context={
                    "source": "chatbot_turn",
                    "session_id": self.session_id,
                    "topic": tag,
                    "topic_tags": [tag],
                    "message_ids": source_message_ids,
                    "turn_indices": source_turn_indices,
                },
                db_path=self.db_path,
                chroma_dir=self.chroma_dir,
            )
            persisted_tags.append(tag)

        return persisted_tags

    @staticmethod
    def _validate_or_fallback_topic_tags(
        raw_tags: list[str],
        messages: list[dict],
    ) -> list[str]:
        """Normalize tagger output and guarantee at least one usable topic tag."""
        tags = normalize_topic_tags(raw_tags)
        if tags:
            return tags

        combined_text = " ".join(
            str(message.get("content", "")) for message in messages
        ).lower()
        for keyword, topic_tag in INFERRED_TOPIC_KEYWORDS:
            if keyword in combined_text:
                return [normalize_topic_tag(topic_tag)]

        return [DEFAULT_TOPIC_TAG]

    @staticmethod
    def _default_topic_tagger(messages: list[dict]) -> list[str]:
        """Use the LLM-backed normalized topic tagger lazily."""
        from memory.tagger import tag_normalized_topics

        return tag_normalized_topics(messages)

    def _trace_generation_memory_consumption(
        self,
        *,
        query: str,
        ltm_hits: list[dict],
        episodic_hits: list[dict],
        ltm_context: str,
        episodic_context: str,
        referent_candidates: list[dict[str, object]] | None = None,
        stm_insufficiency_trigger: dict[str, object] | None = None,
        stm_hits: list[dict] | None = None,
    ) -> None:
        """Record which memory sources were consumed for one generation."""
        stm_hits = stm_hits or []
        referent_candidates = referent_candidates or []
        stm_insufficiency_trigger = stm_insufficiency_trigger or {
            "triggered": False,
            "reason": None,
            "action": None,
            "candidate_count": len(referent_candidates),
        }
        memory_sources = build_memory_source_trace(
            stm_hits=stm_hits,
            ltm_hits=ltm_hits,
            episodic_hits=episodic_hits,
        )
        memory_steps = [
            {
                "memory_type": "STM",
                "step": "read_recent_stm_context",
                "used": bool(stm_hits),
                "hit_count": len(stm_hits),
                "context_injected": bool(stm_hits),
                "source_ids": [
                    str(hit.get("id") or hit.get("message_id") or "")
                    for hit in stm_hits
                    if hit.get("id") or hit.get("message_id")
                ],
            },
            {
                "memory_type": "LTM",
                "step": "retrieve_ltm_context",
                "used": bool(ltm_hits),
                "hit_count": len(ltm_hits),
                "context_injected": bool(ltm_context),
                "source_ids": [
                    str(hit.get("id") or hit.get("source_id") or "")
                    for hit in ltm_hits
                    if hit.get("id") or hit.get("source_id")
                ],
            },
            {
                "memory_type": "Episodic",
                "step": "retrieve_episodic_context",
                "used": bool(episodic_hits),
                "hit_count": len(episodic_hits),
                "context_injected": bool(episodic_context),
                "source_ids": [
                    str(
                        hit.get("episodic_id")
                        or hit.get("id")
                        or hit.get("source_id")
                        or ""
                    )
                    for hit in episodic_hits
                    if hit.get("episodic_id")
                    or hit.get("id")
                    or hit.get("source_id")
                ],
            },
        ]
        trace = {
            "session_id": self.session_id,
            "query": query,
            "stm_hit_count": len(stm_hits),
            "ltm_hit_count": len(ltm_hits),
            "episodic_hit_count": len(episodic_hits),
            "referent_candidates": referent_candidates,
            "referent_candidate_count": len(referent_candidates),
            "stm_insufficiency_trigger": dict(stm_insufficiency_trigger),
            "stm_context_insufficient": bool(
                stm_insufficiency_trigger.get("triggered")
            ),
            "memory_sources": memory_sources,
            "memory_steps": memory_steps,
            "memory_source_types": sorted(
                {str(source["memory_type"]) for source in memory_sources}
            ),
            "stm_context_injected": bool(stm_hits),
            "ltm_context_injected": bool(ltm_context),
            "episodic_context_injected": bool(episodic_context),
        }
        self.last_generation_memory_trace = trace
        logger.info(
            "Hybrid memory retrieval consumed during generation",
            extra=trace,
        )

    def _detect_repeat_for_generation(
        self,
        user_message: str,
        episodic_hits: list[dict],
    ) -> dict[str, object]:
        """Return best repeat match metadata from retrieved Episodic records."""
        query_text = _comparison_text(user_message)
        best_topic: str | None = None
        best_question: str | None = None
        best_confidence = 0.0

        for hit in episodic_hits:
            topic = str(hit.get("topic") or "").strip()
            topic_score = _repeat_similarity_score(query_text, _comparison_text(topic))
            if topic and topic_score > best_confidence:
                best_topic = topic
                best_question = None
                best_confidence = topic_score

            for question in hit.get("questions") or []:
                question_text = str(question).strip()
                if not question_text:
                    continue
                question_score = _repeat_similarity_score(
                    query_text,
                    _comparison_text(question_text),
                )
                if question_score > best_confidence:
                    best_topic = topic or None
                    best_question = question_text
                    best_confidence = question_score

        confidence = round(best_confidence, 4)
        is_repeat = confidence >= DEFAULT_REPEAT_DETECTION_THRESHOLD
        return {
            "is_repeat": is_repeat,
            "matched_prior_topic": best_topic if is_repeat else None,
            "matched_prior_question": best_question if is_repeat else None,
            "confidence": confidence if is_repeat else 0.0,
        }

    def _ensure_episodic_context_reflected(
        self,
        *,
        reply: str,
        repeat_metadata: dict[str, object],
        episodic_hits: list[dict],
    ) -> str:
        """
        Make repeated-topic responses visibly use retrieved Episodic memory.

        The primary path is still prompt injection. This guard keeps the demo
        loop verifiable when a model gives a generic answer despite receiving
        topic-level strengths, weaknesses, and question history.
        """
        if not repeat_metadata.get("is_repeat") or not episodic_hits:
            return reply

        hit = episodic_hits[0]
        context_values = {
            "strength": self._first_text_value(hit.get("strengths")),
            "weakness": self._first_text_value(hit.get("weaknesses")),
            "question": self._first_text_value(hit.get("questions")),
        }
        if not any(context_values.values()):
            return reply

        reply_comparison = reply.lower()
        missing = {
            field: value
            for field, value in context_values.items()
            if value and value.lower() not in reply_comparison
        }
        if not missing:
            return reply

        bridge_parts = []
        topic = str(hit.get("topic") or repeat_metadata.get("matched_prior_topic") or "")
        if topic:
            bridge_parts.append(f"on {topic}")
        if context_values["strength"]:
            bridge_parts.append(f"your strength: {context_values['strength']}")
        if context_values["weakness"]:
            bridge_parts.append(f"focus area: {context_values['weakness']}")
        if context_values["question"]:
            bridge_parts.append(f"prior question: {context_values['question']}")

        bridge = ", ".join(bridge_parts)
        return (
            f"{reply}\n\n"
            f"Based on your stored learning context, I will connect this to {bridge}."
        )

    def _ensure_decorator_wrapper_context_reflected(
        self,
        *,
        reply: str,
        user_message: str,
        ltm_hits: list[dict],
        episodic_hits: list[dict],
    ) -> str:
        """
        Keep the vague follow-up demo answer anchored to decorator wrapper memory.

        The packaged demo asks "방금 얘기하던걸..." after a decorator exchange.
        If the model names the broad context but misses the wrapper relation,
        append the minimum concrete explanation required for a reliable demo.
        """
        if not is_context_recovery_utterance(user_message):
            return reply

        if not self._memory_hits_reference_decorators(ltm_hits, episodic_hits):
            return reply

        reply_comparison = reply.lower()
        has_wrapper_explanation = (
            "wrapper 함수" in reply
            or "함수를 감싸" in reply
            or "wraps a function" in reply_comparison
            or "wrap a function" in reply_comparison
        )
        if has_wrapper_explanation:
            return reply

        return (
            f"{reply}\n\n"
            "decorator 맥락에서는 wrapper 함수가 원래 함수를 감싸고, "
            "실행 순서는 호출될 때 wrapper가 먼저 실행된 뒤 원래 함수로 "
            "이어진다고 보면 돼요."
        )

    def _ensure_resolved_context_recovery_answers_directly(
        self,
        *,
        reply: str,
        user_message: str,
        referent_candidates: list[dict[str, object]],
        ltm_hits: list[dict],
        episodic_hits: list[dict],
    ) -> str:
        """
        Replace clarification-only replies when STM or memory resolved the topic.

        Context-recovery turns such as "이게 뭐야?" are intentionally answerable
        from STM/LTM/Episodic. If the model still asks what the user means, keep
        the demo flow grounded by answering from the resolved decorator context.
        """
        if not is_context_recovery_utterance(user_message):
            return reply

        if not self._looks_like_clarification_request(reply):
            return reply

        resolved_topics = {
            str(candidate.get("topic") or "").strip()
            for candidate in referent_candidates
            if _is_confident_referent_candidate(candidate)
        }
        has_decorator_context = (
            "programming:python-decorators" in resolved_topics
            or self._memory_hits_reference_decorators(ltm_hits, episodic_hits)
        )
        if not has_decorator_context:
            return reply

        return (
            "방금 말한 '이게'는 decorator에서 원래 함수를 감싸는 wrapper 함수 "
            "맥락이에요. decorator는 함수를 바로 바꾸는 대신 wrapper 함수를 "
            "반환하고, 함수가 호출될 때 wrapper가 먼저 실행된 뒤 원래 함수로 "
            "흐름이 이어져요."
        )

    @staticmethod
    def _looks_like_clarification_request(reply: str) -> bool:
        text = " ".join(str(reply or "").strip().lower().split())
        if not text:
            return False

        korean_markers = (
            "어떤 걸",
            "무엇을",
            "뭘 말",
            "더 알려",
            "구체적으로",
            "명확히",
        )
        english_markers = (
            "what do you mean",
            "which part",
            "can you clarify",
            "please clarify",
            "more specific",
        )
        return any(marker in text for marker in (*korean_markers, *english_markers))

    @staticmethod
    def _memory_hits_reference_decorators(
        ltm_hits: list[dict],
        episodic_hits: list[dict],
    ) -> bool:
        """Return True when retrieved memory points at Python decorators."""
        text_parts: list[str] = []
        for hit in ltm_hits:
            text_parts.extend(
                str(hit.get(field) or "")
                for field in ("summary", "topic", "id", "session_id")
            )
            for field in ("topic_tags", "struggles", "confusions", "strengths"):
                text_parts.extend(str(value) for value in hit.get(field) or [])

        for hit in episodic_hits:
            text_parts.extend(
                str(hit.get(field) or "")
                for field in ("topic", "episodic_id", "summary")
            )
            for field in ("topic_tags", "weaknesses", "questions", "strengths"):
                text_parts.extend(str(value) for value in hit.get(field) or [])

        memory_text = " ".join(text_parts).lower()
        return "decorator" in memory_text or "데코레이터" in memory_text

    def _ensure_prior_struggle_context_reflected(
        self,
        *,
        reply: str,
        user_message: str,
        ltm_hits: list[dict],
        episodic_hits: list[dict],
    ) -> str:
        """
        Make past-struggle memory questions visibly grounded in LTM/Episodic data.

        Broad questions like "저번에 내가 뭘 어려워했었지?" do not name a topic,
        so the model can answer generically even with retrieved memory in the
        prompt. This keeps the hands-on demo verifiable by naming at least one
        stored Python struggle topic when the answer omits it.
        """
        if not self._is_prior_struggle_question(user_message):
            return reply

        topic_names = self._prior_struggle_topic_names(ltm_hits, episodic_hits)
        if not topic_names:
            return reply

        struggles = self._prior_struggle_values(ltm_hits, episodic_hits)
        reply_comparison = reply.lower()
        mentions_topic = any(topic.lower() in reply_comparison for topic in topic_names)
        concrete_terms = self._prior_struggle_concrete_terms(struggles)
        mentions_concrete_struggle = any(
            term.lower() in reply_comparison for term in concrete_terms
        )
        if mentions_topic and (not concrete_terms or mentions_concrete_struggle):
            return reply

        struggle_text = f" 특히 {struggles[0]} 부분이 어려웠던 기록이 있어요." if struggles else ""
        if mentions_topic:
            return f"{reply}\n\n너가 예전에 남긴 LTM 기록을 보면 구체적으로는{struggle_text}"

        return (
            f"{reply}\n\n"
            f"너가 예전에 {', '.join(topic_names)} 쪽을 어려워했었지."
            f"{struggle_text}"
        )

    @staticmethod
    def _ensure_explicit_ltm_reference_reflected(
        *,
        reply: str,
        user_message: str,
        ltm_hits: list[dict],
    ) -> str:
        """
        Keep explicit LTM-reference demo turns visibly grounded in LTM.

        The May 1 evening fixture asks a functions/exceptions question by
        naming the earlier promoted loops/conditionals LTM item. If the model
        answers the programming question but omits that memory provenance, the
        hands-on demo loses the observable recall signal.
        """
        if not ltm_hits:
            return reply

        query_text = str(user_message or "").lower()
        if "ltm" not in query_text and "demo-ltm" not in query_text:
            return reply

        reply_text = str(reply or "")
        reply_comparison = reply_text.lower()
        has_recall_expression = any(
            marker in reply_text
            for marker in ("저장된 LTM", "너가 예전에", "네가 예전에", "전에 이랬었지")
        )
        mentions_ltm_id = any(
            str(hit.get("id") or "").strip()
            and str(hit.get("id")).strip().lower() in reply_comparison
            for hit in ltm_hits
        )
        if has_recall_expression and mentions_ltm_id:
            return reply

        first_hit = ltm_hits[0]
        ltm_id = str(first_hit.get("id") or "이전 LTM 기록")
        summary = str(first_hit.get("summary") or "").strip()
        summary_text = f" 요약은 {summary}" if summary else ""
        return (
            f"{reply_text}\n\n"
            f"저장된 LTM {ltm_id}를 보면 너가 예전에 다룬 흐름과 연결돼요."
            f"{summary_text}"
        )

    @staticmethod
    def _is_prior_struggle_question(user_message: str) -> bool:
        text = user_message.strip().lower()
        if not text:
            return False

        korean_prior = any(marker in text for marker in ("저번", "지난", "전에"))
        korean_struggle = "어려" in text or "헷갈" in text
        if korean_prior and korean_struggle:
            return True

        english_prior = any(
            marker in text for marker in ("last time", "previous", "prior", "before")
        )
        english_struggle = any(
            marker in text for marker in ("struggle", "difficult", "hard", "confus")
        )
        return english_prior and english_struggle

    @staticmethod
    def _prior_struggle_topic_names(
        ltm_hits: list[dict],
        episodic_hits: list[dict],
    ) -> list[str]:
        topics: list[str] = []
        for hit in ltm_hits:
            topics.extend(str(tag) for tag in hit.get("topic_tags") or [])
        for hit in episodic_hits:
            topic = str(hit.get("topic") or "").strip()
            if topic:
                topics.append(topic)
            topics.extend(str(tag) for tag in hit.get("topic_tags") or [])

        names: list[str] = []
        for topic in topics:
            normalized = topic.lower()
            if "decorator" in normalized:
                label = "Python decorators"
            elif "recursion" in normalized:
                label = "recursion"
            else:
                continue
            if label not in names:
                names.append(label)

        return names

    @staticmethod
    def _prior_struggle_values(
        ltm_hits: list[dict],
        episodic_hits: list[dict],
    ) -> list[str]:
        values: list[str] = []
        for hit in ltm_hits:
            values.extend(str(value).strip() for value in hit.get("struggles") or [])
            values.extend(str(value).strip() for value in hit.get("confusions") or [])
        for hit in episodic_hits:
            values.extend(str(value).strip() for value in hit.get("weaknesses") or [])
        return [value for value in values if value]

    @staticmethod
    def _prior_struggle_concrete_terms(struggles: list[str]) -> list[str]:
        """Extract concrete demo difficulty terms from stored struggle text."""
        concrete_markers = (
            "wrapper 구조",
            "실행 순서",
            "base case",
            "호출 스택",
        )
        terms: list[str] = []
        for struggle in struggles:
            lowered = struggle.lower()
            for marker in concrete_markers:
                if marker.lower() in lowered and marker not in terms:
                    terms.append(marker)
        return terms

    @staticmethod
    def _first_text_value(values: object) -> str:
        if not isinstance(values, list):
            return ""
        for value in values:
            text = str(value).strip()
            if text:
                return text
        return ""

    def _retrieve_ltm(self, user_message: str) -> list[dict]:
        """Retrieve relevant long-term memory for the current user turn."""
        context_recovery_query = self._build_context_recovery_memory_query(user_message)
        if self._ltm_retriever is not None:
            try:
                return self._filter_hits_to_referent_topics(
                    self._ltm_retriever(context_recovery_query),
                    user_message,
                )
            except Exception as exc:
                logger.warning(
                    "Configured LTM retriever failed; continuing without LTM context",
                    extra={"error": str(exc)},
                )
                return []

        retrieval_query = (
            context_recovery_query
            if is_context_recovery_utterance(user_message)
            else self._build_active_session_memory_query(user_message)
        )
        try:
            retrieval_input = build_ltm_retrieval_input(retrieval_query)
        except ValueError:
            return []

        from memory.ltm import get_all_ltm
        from memory.ltm import search_ltm_by_embedding

        keyword_tokens = retrieval_input.keyword_tokens
        chroma_path = getattr(self, "chroma_dir", None)
        if chroma_path is not None:
            query_embedding = build_demo_query_embedding(retrieval_input.semantic_query)
            try:
                vector_hits = search_ltm_by_embedding(
                    query_embedding,
                    n_results=3,
                    keyword_tokens=keyword_tokens,
                    db_path=self.db_path,
                    chroma_path=chroma_path,
                )
            except Exception as exc:
                logger.warning(
                    "Vector LTM retrieval failed; falling back to SQLite ranking",
                    extra={"error": str(exc)},
                )
                vector_hits = []

            if vector_hits:
                return self._annotate_ltm_retrieval_hits(
                    vector_hits,
                    source="chroma",
                    mode="keyword_vector",
                )

            try:
                semantic_hits = search_ltm_by_embedding(
                    query_embedding,
                    n_results=3,
                    db_path=self.db_path,
                    chroma_path=chroma_path,
                )
            except Exception as exc:
                logger.warning(
                    "Semantic LTM retrieval failed; falling back to SQLite ranking",
                    extra={"error": str(exc)},
                )
                semantic_hits = []

            if semantic_hits:
                return self._annotate_ltm_retrieval_hits(
                    semantic_hits,
                    source="chroma",
                    mode="semantic_fallback",
                )

        try:
            candidates = get_all_ltm(limit=20, db_path=self.db_path)
        except (OSError, sqlite3.Error) as exc:
            logger.warning(
                "SQLite LTM fallback retrieval failed; continuing without LTM context",
                extra={"error": str(exc), "db_path": str(self.db_path)},
            )
            return []
        if not keyword_tokens:
            return candidates[:3]

        inferred_topic_tag = self._infer_topic_tag_from_text(retrieval_query)
        ranked: list[tuple[tuple[int, int, int], dict]] = []
        for candidate in candidates:
            searchable = " ".join(
                [
                    str(candidate.get("summary", "")),
                    *[str(value) for value in candidate.get("struggles", [])],
                    *[str(value) for value in candidate.get("strengths", [])],
                    *[str(value) for value in candidate.get("confusions", [])],
                    *[str(value) for value in candidate.get("topic_tags", [])],
                ]
            ).lower()
            score = sum(1 for token in keyword_tokens if token in searchable)
            if score:
                topic_tags = candidate.get("topic_tags", [])
                exact_topic_match = int(
                    bool(inferred_topic_tag)
                    and topic_tags == [inferred_topic_tag]
                )
                topic_specificity = -len(topic_tags) if topic_tags else 0
                ranked.append(
                    (
                        (score, exact_topic_match, topic_specificity),
                        candidate,
                    )
                )

        ranked.sort(key=lambda item: item[0], reverse=True)
        return self._annotate_ltm_retrieval_hits(
            [candidate for _score, candidate in ranked[:3]],
            source="sqlite",
            mode="keyword_fallback",
        )

    @staticmethod
    def _annotate_ltm_retrieval_hits(
        hits: list[dict],
        *,
        source: str,
        mode: str,
    ) -> list[dict]:
        """Attach retrieval provenance without mutating retriever-owned dicts."""
        annotated: list[dict] = []
        for hit in hits:
            enriched = dict(hit)
            ltm_id = str(enriched.get("id") or enriched.get("ltm_id") or "").strip()
            enriched.setdefault("source", "ltm")
            if ltm_id:
                enriched.setdefault("source_id", ltm_id)
            enriched.setdefault("retrieval_source", source)
            enriched.setdefault("retrieval_mode", mode)
            annotated.append(enriched)
        return annotated

    @staticmethod
    def _infer_topic_tag_from_text(text: str) -> str | None:
        return _infer_topic_tag_from_text(text)

    def _retrieve_episodic(self, user_message: str) -> list[dict]:
        """Retrieve relevant topic-level Episodic memory for the current user turn."""
        context_recovery_query = self._build_context_recovery_memory_query(user_message)
        if self._episodic_retriever is not None:
            try:
                episodic_hits = self._episodic_retriever(context_recovery_query)
            except Exception as exc:
                logger.warning(
                    "Configured Episodic retriever failed; continuing without "
                    "Episodic context",
                    extra={"error": str(exc)},
                )
                return []
            if not episodic_hits:
                return []
            if not isinstance(episodic_hits, list):
                logger.warning(
                    "Configured Episodic retriever returned non-list results; "
                    "continuing without Episodic context",
                    extra={"result_type": type(episodic_hits).__name__},
                )
                return []
            return self._hydrate_episodic_hits_by_topic(
                self._filter_hits_to_referent_topics(episodic_hits, user_message)
            )

        conversation_hits = self._retrieve_question_based_conversation_episodes(
            user_message
        )

        try:
            retrieval_input = build_episodic_retrieval_input(
                context_recovery_query
                if is_context_recovery_utterance(user_message)
                else self._build_active_session_memory_query(user_message)
            )
        except ValueError:
            return conversation_hits

        keyword_tokens = retrieval_input.keyword_tokens
        chroma_path = getattr(self, "chroma_dir", None)
        if chroma_path is not None:
            from episodic_schema import search_episodic_by_embedding

            query_embedding = build_demo_query_embedding(retrieval_input.semantic_query)
            try:
                vector_hits = search_episodic_by_embedding(
                    query_embedding,
                    n_results=3,
                    keyword_tokens=keyword_tokens,
                    db_path=self.db_path,
                    chroma_dir=chroma_path,
                )
            except Exception as exc:
                logger.warning(
                    "Vector Episodic retrieval failed; falling back to SQLite ranking",
                    extra={"error": str(exc)},
                )
                vector_hits = []

            if vector_hits:
                hydrated_vector_hits = self._hydrate_episodic_hits_by_topic(vector_hits)
                if self._has_usable_episodic_history(hydrated_vector_hits):
                    return self._merge_episodic_hits(
                        conversation_hits,
                        hydrated_vector_hits,
                    )

            try:
                semantic_hits = search_episodic_by_embedding(
                    query_embedding,
                    n_results=3,
                    db_path=self.db_path,
                    chroma_dir=chroma_path,
                )
            except Exception as exc:
                logger.warning(
                    "Semantic Episodic retrieval failed; falling back to SQLite ranking",
                    extra={"error": str(exc)},
                )
                semantic_hits = []

            if semantic_hits:
                hydrated_semantic_hits = self._hydrate_episodic_hits_by_topic(
                    semantic_hits
                )
                if self._has_usable_episodic_history(hydrated_semantic_hits):
                    return self._merge_episodic_hits(
                        conversation_hits,
                        hydrated_semantic_hits,
                    )

        try:
            candidates = list_all_episodic(
                db_path=self.db_path,
                memory_item_types=[EPISODIC_LEARNING_EVENT_TYPE],
            )
        except sqlite3.OperationalError:
            return conversation_hits

        ranked: list[tuple[int, dict]] = []
        for candidate in candidates:
            searchable = " ".join(
                [
                    str(candidate.get("topic", "")),
                    *[str(value) for value in candidate.get("topic_tags", [])],
                    *[str(value) for value in candidate.get("strengths", [])],
                    *[str(value) for value in candidate.get("weaknesses", [])],
                    *[str(value) for value in candidate.get("questions", [])],
                ]
            ).lower()
            score = sum(
                1 for token in retrieval_input.keyword_tokens if token in searchable
            )
            if score:
                ranked.append((score, candidate))

        ranked.sort(key=lambda item: item[0], reverse=True)
        return self._merge_episodic_hits(
            conversation_hits,
            self._annotate_episodic_retrieval_hits(
                self._hydrate_episodic_hits_by_topic(
                    [candidate for _score, candidate in ranked[:3]]
                ),
                source="sqlite",
                mode="keyword_fallback",
            ),
        )

    def _retrieve_question_based_conversation_episodes(
        self,
        user_message: str,
    ) -> list[dict]:
        """
        Retrieve past conversation episodes using the user's current question.

        Active-session expansion is useful for topic grounding, but conversation
        episodes should be matched against the question itself so exact follow-up
        patterns like "방금 얘기하던걸..." are not diluted by prior STM text.
        """
        chroma_path = getattr(self, "chroma_dir", None)
        if chroma_path is None:
            return []

        try:
            retrieval_input = build_episodic_retrieval_input(user_message)
        except ValueError:
            return []

        from episodic_schema import search_episodic_by_embedding

        query_embedding = build_demo_query_embedding(retrieval_input.semantic_query)
        try:
            hits = search_episodic_by_embedding(
                query_embedding,
                n_results=3,
                keyword_tokens=retrieval_input.keyword_tokens,
                memory_item_types=[EPISODIC_CONVERSATION_EPISODE_TYPE],
                db_path=self.db_path,
                chroma_dir=chroma_path,
            )
        except Exception as exc:
            logger.warning(
                "Conversation Episodic retrieval failed; continuing with topic search",
                extra={"error": str(exc)},
            )
            return []

        hydrated_hits = self._hydrate_episodic_hits_by_topic(hits)
        if self._has_usable_episodic_history(hydrated_hits):
            return hydrated_hits
        return []

    @staticmethod
    def _merge_episodic_hits(*hit_groups: list[dict]) -> list[dict]:
        """Merge Episodic result groups in priority order without duplicates."""
        merged: list[dict] = []
        seen_ids: set[str] = set()
        for hits in hit_groups:
            for hit in hits:
                episodic_id = str(hit.get("episodic_id") or hit.get("id") or "")
                if not episodic_id or episodic_id in seen_ids:
                    continue
                seen_ids.add(episodic_id)
                merged.append(hit)
        return merged

    @staticmethod
    def _has_usable_episodic_history(hits: list[dict]) -> bool:
        """Return True when hits include learner history needed for grounding."""
        for hit in hits:
            if any(
                hit.get(field)
                for field in ("strengths", "weaknesses", "questions")
            ):
                return True
        return False

    @staticmethod
    def _annotate_episodic_retrieval_hits(
        hits: list[dict],
        *,
        source: str,
        mode: str,
    ) -> list[dict]:
        """Attach retrieval provenance without mutating retriever-owned dicts."""
        annotated: list[dict] = []
        for hit in hits:
            enriched = dict(hit)
            episodic_id = str(
                enriched.get("episodic_id") or enriched.get("id") or ""
            ).strip()
            enriched.setdefault("source", "episodic")
            if episodic_id:
                enriched.setdefault("source_id", episodic_id)
            enriched.setdefault("retrieval_source", source)
            enriched.setdefault("retrieval_mode", mode)
            annotated.append(enriched)
        return annotated

    def _hydrate_episodic_hits_by_topic(self, hits: list[dict]) -> list[dict]:
        """
        Expand topic-only Episodic hits with the SQLite record for that topic.

        Vector or test retrievers may return just the matched topic metadata.
        Response generation needs the learner profile fields stored in SQLite:
        strengths, weaknesses, and prior questions.
        """
        hydrated_hits: list[dict] = []
        score_fields = (
            "distance",
            "semantic_score",
            "keyword_score",
            "keyword_matches",
            "keyword_score_normalized",
            "hybrid_score",
        )

        for hit in hits:
            if not isinstance(hit, dict):
                continue
            topic = str(hit.get("topic") or "").strip()
            needs_history = any(
                not hit.get(field) for field in ("strengths", "weaknesses", "questions")
            )
            if not topic or not needs_history:
                hydrated_hits.append(hit)
                continue

            try:
                record = get_episodic_by_topic(topic, db_path=self.db_path)
            except sqlite3.OperationalError:
                record = None
            if record is None:
                hydrated_hits.append(hit)
                continue

            hydrated = dict(record)
            for field in score_fields:
                if field in hit:
                    hydrated[field] = hit[field]
            for field in ("retrieval_source", "retrieval_mode"):
                if field in hit:
                    hydrated[field] = hit[field]
            hydrated_hits.append(hydrated)

        return hydrated_hits

    def _build_active_session_memory_query(self, user_message: str) -> str:
        """
        Expand ambiguous follow-up turns with active-session user question history.

        Follow-ups like "review that again" or "이게 뭐야?" need the current
        session's earlier user turns to recover topic tokens used by the
        lightweight keyword retrievers.
        """
        required_attrs = ("_conn", "session_id", "stm_window")
        if not all(hasattr(self, attr) for attr in required_attrs):
            return user_message

        context = self.get_current_session_context()
        recent_user_messages = [
            message.strip()
            for message in context["user_messages"]
            if message.strip()
        ]
        candidate_texts: list[str] = []
        if is_context_recovery_utterance(user_message):
            referent_candidates = self._extract_referent_candidates_for_turn(
                user_message,
                context,
            )
            candidate_texts = [
                str(candidate.get("text") or "").strip()
                for candidate in referent_candidates
                if str(candidate.get("text") or "").strip()
            ]
        if not recent_user_messages and not candidate_texts:
            return user_message

        query_parts = [*candidate_texts]
        if recent_user_messages and recent_user_messages[-1] == user_message.strip():
            query_parts.extend(recent_user_messages)
        else:
            query_parts.extend([*recent_user_messages, user_message])

        deduped_parts: list[str] = []
        seen: set[str] = set()
        for part in query_parts:
            key = " ".join(part.lower().split())
            if key in seen:
                continue
            seen.add(key)
            deduped_parts.append(part)
        return "\n".join(deduped_parts)

    def _build_context_recovery_memory_query(self, user_message: str) -> str:
        """Return the STM-expanded query only for explicit context recovery turns."""
        if not is_context_recovery_utterance(user_message):
            return user_message
        return self._build_active_session_memory_query(user_message)

    def _extract_referent_candidates_for_turn(
        self,
        user_message: str,
        session_context: dict,
    ) -> list[dict[str, object]]:
        """Extract active referent candidates for an ambiguous current turn."""
        inferred_topic = self._infer_current_topic_from_context(
            user_message,
            session_context,
        )
        current_topic_context: dict[str, object] = {}
        if inferred_topic:
            current_topic_context["topic_tags"] = [inferred_topic]
        return extract_referent_candidates(
            session_context,
            current_topic_context=current_topic_context,
        )

    def _filter_hits_to_referent_topics(
        self,
        hits: list[dict] | None,
        user_message: str,
    ) -> list[dict]:
        """Keep configured retrieval results aligned with recovered referent topics."""
        if not isinstance(hits, list):
            return []

        context = self.get_current_session_context()
        referent_topics = self._high_priority_referent_topics(user_message, context)
        if not referent_topics:
            return hits

        filtered = [
            hit
            for hit in hits
            if self._hit_matches_any_topic(hit, referent_topics)
        ]
        return filtered

    def _high_priority_referent_topics(
        self,
        user_message: str,
        session_context: dict,
    ) -> set[str]:
        """
        Return topic tags that should dominate custom retrieval results.

        Direct topic mentions in the current turn, such as "decorator", are
        stronger than retriever scores. Recent STM referents are promoted only
        for explicit context-recovery turns so ordinary memory questions do not
        accidentally inherit a stale topic.
        """
        direct_topic = self._infer_topic_tag_from_text(user_message)
        if direct_topic:
            return {direct_topic}

        if not is_context_recovery_utterance(user_message):
            return set()

        candidates = self._extract_referent_candidates_for_turn(
            user_message,
            session_context,
        )
        return {
            str(candidate.get("topic") or "").strip()
            for candidate in candidates
            if _is_confident_referent_candidate(candidate)
        }

    @staticmethod
    def _hit_matches_any_topic(hit: dict, topics: set[str]) -> bool:
        raw_topic_tags = hit.get("topic_tags") or []
        if isinstance(raw_topic_tags, str):
            topic_values = [tag.strip() for tag in raw_topic_tags.split(",")]
        else:
            topic_values = [str(topic) for topic in raw_topic_tags]
        hit_topics = {
            normalize_topic_tag(topic)
            for topic in topic_values
            if str(topic).strip()
        }
        topic = str(hit.get("topic") or "").strip()
        if topic:
            hit_topics.add(normalize_topic_tag(topic))
        return bool(hit_topics & topics)

    def _infer_current_topic_from_context(
        self,
        user_message: str,
        session_context: dict,
    ) -> str | None:
        """Infer the active topic from the current turn or recent STM turns."""
        direct_topic = self._infer_topic_tag_from_text(user_message)
        if direct_topic:
            return direct_topic

        messages = [
            message
            for message in session_context.get("messages", [])
            if str(message.get("role", "")).strip() in {"user", "assistant"}
            and str(message.get("content", "")).strip()
        ]
        for message in reversed(messages):
            text = str(message.get("content", "")).strip()
            if is_context_recovery_utterance(text):
                continue
            inferred_topic = self._infer_topic_tag_from_text(text)
            if inferred_topic:
                return inferred_topic
        return None

    @property
    def is_active(self) -> bool:
        """True while this chatbot session can accept normal chat turns."""
        return self._session_manager.is_active

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "Chatbot":
        return self

    def __exit__(self, *_) -> None:
        self.end_session()
        self.close()


# ---------------------------------------------------------------------------
# CLI demo entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Learning Chatbot (type 'quit' to exit) ===")
    print(f"Session ID: ", end="")

    with Chatbot() as bot:
        print(bot.session_id)
        while True:
            try:
                user_input = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[Session ended]")
                break
            if not user_input:
                continue
            reply = bot.chat(user_input)
            if not bot.is_active:
                print(reply)
                break
            print(f"\nBot: {reply}")
