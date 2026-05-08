"""
Episodic Memory Schema
======================
Episodic memory stores topic-level knowledge about the user:
per-topic strengths, weaknesses, and question history.

Fields
------
Required SQLite fields:
- episodic_id  : UUID primary key
- topic        : LLM-classified topic label (e.g. "Python decorators")
- topic_tags   : JSON list of canonical topic tags related to this episode
- strengths    : JSON list – things the user does well on this topic
- weaknesses   : JSON list – areas where the user struggles on this topic
- questions    : JSON list – questions the user has asked on this topic
- source_session_ids   : JSON list of sessions that contributed evidence
- source_message_ids   : JSON list of STM message IDs that contributed evidence
- source_turn_indices  : JSON list of source STM turn indices
- source_message_timestamps : JSON list of source STM message timestamps
- topic_contexts       : JSON list of compact context snapshots for tag evidence
- occurrence_count     : number of upserts/evidence observations for the topic
- memory_item_type     : learning_event or conversation_episode item category
- last_message_timestamp : ISO-8601 timestamp of the latest source turn
- last_updated : ISO-8601 timestamp of the most recent upsert

Optional SQLite fields:
- topic_embedding : JSON vector mirrored in Chroma when available
"""

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from memory.retrieval import (
    DEFAULT_HYBRID_SCORE_CONFIG,
    HybridScoreConfig,
    calculate_hybrid_score,
    normalize_keyword_match_score,
    normalize_semantic_score,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "memory.db"
CHROMA_DIR = BASE_DIR / "chroma_db"
EPISODIC_CHROMA_QUERY_INCLUDE_FIELDS = (
    "documents",
    "metadatas",
    "distances",
)
EPISODIC_CHROMA_QUERY_RESULT_FIELDS = (
    "ids",
    "documents",
    "metadatas",
    "distances",
)

# ---------------------------------------------------------------------------
# Episodic topic taxonomy and normalization
# ---------------------------------------------------------------------------

TOPIC_TAG_TAXONOMY: dict[str, dict[str, Any]] = {
    "programming": {
        "label": "Programming",
        "description": "Programming languages, syntax, debugging, and code design.",
        "canonical_examples": [
            "programming:python-decorators",
            "programming:recursion",
            "programming:async-await",
        ],
    },
    "data_ai": {
        "label": "Data and AI",
        "description": "Machine learning, AI, data analysis, and model behavior.",
        "canonical_examples": [
            "data_ai:machine-learning",
            "data_ai:embeddings",
            "data_ai:prompt-engineering",
        ],
    },
    "computer_science": {
        "label": "Computer Science",
        "description": "Algorithms, data structures, systems, and theory topics.",
        "canonical_examples": [
            "computer_science:graphs",
            "computer_science:time-complexity",
            "computer_science:operating-systems",
        ],
    },
    "math_statistics": {
        "label": "Math and Statistics",
        "description": "Mathematics, probability, statistics, and quantitative reasoning.",
        "canonical_examples": [
            "math_statistics:statistics",
            "math_statistics:linear-algebra",
            "math_statistics:probability",
        ],
    },
    "web_app": {
        "label": "Web Applications",
        "description": "Frontend, backend, APIs, databases, and web product behavior.",
        "canonical_examples": [
            "web_app:react-components",
            "web_app:sqlite-schema",
            "web_app:http-apis",
        ],
    },
    "tools_workflow": {
        "label": "Tools and Workflow",
        "description": "Developer tools, environments, testing, version control, and workflow.",
        "canonical_examples": [
            "tools_workflow:pytest",
            "tools_workflow:git",
            "tools_workflow:conda-environments",
        ],
    },
    "learning_strategy": {
        "label": "Learning Strategy",
        "description": "Study habits, metacognition, practice plans, and question patterns.",
        "canonical_examples": [
            "learning_strategy:debugging-practice",
            "learning_strategy:spaced-repetition",
            "learning_strategy:concept-review",
        ],
    },
    "domain_knowledge": {
        "label": "Domain Knowledge",
        "description": "Subject-matter topics outside the core technical learning taxonomy.",
        "canonical_examples": [
            "domain_knowledge:finance",
            "domain_knowledge:biology",
            "domain_knowledge:business",
        ],
    },
    "general": {
        "label": "General",
        "description": "Fallback category for unclear or uncategorized learning topics.",
        "canonical_examples": [
            "general:uncategorized",
            "general:conversation",
            "general:question",
        ],
    },
}

TOPIC_ALIASES: dict[str, str] = {
    "ai": "artificial intelligence",
    "async await": "async await",
    "js": "javascript",
    "ml": "machine learning",
    "oop": "object oriented programming",
    "stats": "statistics",
    "sqlite3": "sqlite",
}

DEFAULT_REPEAT_DETECTION_THRESHOLD = 0.86

_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "programming": (
        "async",
        "class",
        "code",
        "decorator",
        "function",
        "javascript",
        "python",
        "recursion",
        "variable",
    ),
    "data_ai": (
        "ai",
        "artificial intelligence",
        "chroma",
        "embedding",
        "llm",
        "machine learning",
        "model",
        "prompt",
        "vector",
    ),
    "computer_science": (
        "algorithm",
        "complexity",
        "data structure",
        "graph",
        "operating system",
        "tree",
    ),
    "math_statistics": (
        "algebra",
        "calculus",
        "probability",
        "statistics",
        "stats",
    ),
    "web_app": (
        "api",
        "backend",
        "css",
        "frontend",
        "html",
        "react",
        "sqlite",
        "web",
    ),
    "tools_workflow": (
        "conda",
        "git",
        "pytest",
        "test",
        "terminal",
        "workflow",
    ),
    "learning_strategy": (
        "confusion",
        "practice",
        "review",
        "study",
    ),
}


def _slugify_topic(topic: str) -> str:
    text = topic.strip().lower()
    text = text.replace("c++", "c plus plus").replace("c#", "c sharp")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "uncategorized"


def _canonical_topic_text(topic: str) -> str:
    compact = re.sub(r"\s+", " ", topic.strip().lower())
    compact = re.sub(r"[^\w#+ ]+", " ", compact)
    compact = re.sub(r"\s+", " ", compact).strip()
    return TOPIC_ALIASES.get(compact, compact)


def _infer_topic_category(topic: str) -> str:
    lowered = topic.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return category
    return "general"


def normalize_topic_tag(topic: str, category: str | None = None) -> str:
    """
    Convert a raw LLM topic label into a canonical Episodic topic tag.

    Canonical tags use ``category:topic-slug``. Empty or unclear labels fall
    back to ``general:uncategorized``. Unknown categories are normalized to the
    ``general`` taxonomy bucket.
    """
    raw_topic = topic.strip()
    if ":" in raw_topic:
        raw_category, raw_topic = raw_topic.split(":", 1)
        category = category or raw_category.strip()

    canonical_text = _canonical_topic_text(raw_topic)
    if not canonical_text:
        return "general:uncategorized"

    normalized_category = (category or _infer_topic_category(canonical_text)).strip()
    if normalized_category not in TOPIC_TAG_TAXONOMY:
        normalized_category = "general"

    return f"{normalized_category}:{_slugify_topic(canonical_text)}"


def _normalize_topic_item(item: str | dict[str, Any]) -> tuple[str, str]:
    if isinstance(item, dict):
        raw_topic = str(item.get("topic", ""))
        raw_category = item.get("category")
        tag = normalize_topic_tag(
            raw_topic,
            category=str(raw_category) if raw_category else None,
        )
        return tag, raw_topic.strip()

    raw_topic = str(item)
    return normalize_topic_tag(raw_topic), raw_topic.strip()


def detect_duplicate_topic_tags(
    topics: list[str | dict[str, Any]],
) -> dict[str, list[str]]:
    """
    Return normalized topic tags that appear more than once with their raw aliases.

    This makes LLM duplicate output visible while using the same canonicalization
    rules as Episodic persistence.
    """
    aliases_by_tag: dict[str, list[str]] = {}
    for item in topics:
        tag, raw_topic = _normalize_topic_item(item)
        aliases_by_tag.setdefault(tag, []).append(raw_topic)

    return {
        tag: aliases
        for tag, aliases in aliases_by_tag.items()
        if len(aliases) > 1
    }


def normalize_topic_tags(topics: list[str | dict[str, Any]]) -> list[str]:
    """Normalize a list of raw topic labels and remove duplicates in order."""
    normalized: list[str] = []
    seen: set[str] = set()

    for item in topics:
        tag, _raw_topic = _normalize_topic_item(item)

        if tag not in seen:
            seen.add(tag)
            normalized.append(tag)

    return normalized

# ---------------------------------------------------------------------------
# SQLite – Episodic table
# ---------------------------------------------------------------------------

EPISODIC_DDL = """
CREATE TABLE IF NOT EXISTS episodic_memory (
    episodic_id   TEXT PRIMARY KEY,          -- UUID
    topic         TEXT NOT NULL UNIQUE,      -- LLM-assigned topic label
    topic_tags    TEXT NOT NULL DEFAULT '[]',-- JSON array of canonical tags
    strengths     TEXT NOT NULL DEFAULT '[]',-- JSON array
    weaknesses    TEXT NOT NULL DEFAULT '[]',-- JSON array
    questions     TEXT NOT NULL DEFAULT '[]',-- JSON array
    source_session_ids TEXT NOT NULL DEFAULT '[]', -- JSON array
    source_message_ids TEXT NOT NULL DEFAULT '[]', -- JSON array
    source_turn_indices TEXT NOT NULL DEFAULT '[]',-- JSON array
    source_message_timestamps TEXT NOT NULL DEFAULT '[]',-- JSON array
    topic_contexts TEXT NOT NULL DEFAULT '[]',-- JSON array of context snapshots
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    memory_item_type TEXT NOT NULL DEFAULT 'learning_event',
    last_message_timestamp TEXT,
    topic_embedding TEXT,                    -- JSON vector, mirrored in Chroma
    last_updated  TEXT NOT NULL              -- ISO-8601
);
"""

EPISODIC_LEARNING_EVENT_TYPE = "learning_event"
EPISODIC_CONVERSATION_EPISODE_TYPE = "conversation_episode"
EPISODIC_MEMORY_ITEM_TYPES: set[str] = {
    EPISODIC_LEARNING_EVENT_TYPE,
    EPISODIC_CONVERSATION_EPISODE_TYPE,
}

EPISODIC_REQUIRED_STORAGE_FIELDS: set[str] = {
    "episodic_id",
    "topic",
    "topic_tags",
    "strengths",
    "weaknesses",
    "questions",
    "source_session_ids",
    "source_message_ids",
    "source_turn_indices",
    "source_message_timestamps",
    "topic_contexts",
    "occurrence_count",
    "memory_item_type",
    "last_updated",
}

EPISODIC_OPTIONAL_STORAGE_FIELDS: set[str] = {
    "last_message_timestamp",
    "topic_embedding",
}


def get_db_connection(db_path: str | Path = DB_PATH) -> sqlite3.Connection:
    """Return a SQLite connection with row_factory set."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def create_episodic_table(db_path: str | Path = DB_PATH) -> None:
    """Create the episodic_memory table if it does not already exist."""
    with get_db_connection(db_path) as conn:
        conn.execute(EPISODIC_DDL)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(episodic_memory)")}
        migrations = {
            "topic_tags": "ALTER TABLE episodic_memory ADD COLUMN topic_tags TEXT NOT NULL DEFAULT '[]'",
            "strengths": "ALTER TABLE episodic_memory ADD COLUMN strengths TEXT NOT NULL DEFAULT '[]'",
            "weaknesses": "ALTER TABLE episodic_memory ADD COLUMN weaknesses TEXT NOT NULL DEFAULT '[]'",
            "questions": "ALTER TABLE episodic_memory ADD COLUMN questions TEXT NOT NULL DEFAULT '[]'",
            "source_session_ids": "ALTER TABLE episodic_memory ADD COLUMN source_session_ids TEXT NOT NULL DEFAULT '[]'",
            "source_message_ids": "ALTER TABLE episodic_memory ADD COLUMN source_message_ids TEXT NOT NULL DEFAULT '[]'",
            "source_turn_indices": "ALTER TABLE episodic_memory ADD COLUMN source_turn_indices TEXT NOT NULL DEFAULT '[]'",
            "source_message_timestamps": "ALTER TABLE episodic_memory ADD COLUMN source_message_timestamps TEXT NOT NULL DEFAULT '[]'",
            "topic_contexts": "ALTER TABLE episodic_memory ADD COLUMN topic_contexts TEXT NOT NULL DEFAULT '[]'",
            "occurrence_count": "ALTER TABLE episodic_memory ADD COLUMN occurrence_count INTEGER NOT NULL DEFAULT 1",
            "memory_item_type": "ALTER TABLE episodic_memory ADD COLUMN memory_item_type TEXT NOT NULL DEFAULT 'learning_event'",
            "last_message_timestamp": "ALTER TABLE episodic_memory ADD COLUMN last_message_timestamp TEXT",
            "topic_embedding": "ALTER TABLE episodic_memory ADD COLUMN topic_embedding TEXT",
            "last_updated": "ALTER TABLE episodic_memory ADD COLUMN last_updated TEXT NOT NULL DEFAULT ''",
        }
        for column, statement in migrations.items():
            if column not in cols:
                conn.execute(statement)
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_episodic_memory_item_type
            ON episodic_memory(memory_item_type)
            """
        )
        conn.commit()
    print(f"[Episodic] SQLite table 'episodic_memory' ready at {db_path}")


# ---------------------------------------------------------------------------
# Chroma – Episodic topic-embedding collection
# ---------------------------------------------------------------------------

EPISODIC_COLLECTION_NAME = "episodic_topics"
EPISODIC_CHROMA_METADATA_FIELDS = (
    "episodic_id",
    "topic",
    "topic_tags",
    "source_session_ids",
    "source_message_ids",
    "source_turn_indices",
    "last_message_timestamp",
    "occurrence_count",
    "memory_item_type",
    "last_updated",
)


def get_episodic_chroma_collection(chroma_dir: str | Path = CHROMA_DIR):
    """
    Return (or create) the Chroma collection for Episodic topic embeddings.

    Each document in this collection represents one topic record.
    Metadata fields stored alongside each embedding are defined by
    EPISODIC_CHROMA_METADATA_FIELDS.
    """
    try:
        import chromadb
        from chromadb.config import Settings

        client = chromadb.PersistentClient(
            path=str(chroma_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        collection = client.get_or_create_collection(
            name=EPISODIC_COLLECTION_NAME,
            metadata={
                "description": "Episodic topic embeddings for semantic search",
                "hnsw:space": "cosine",
            },
        )
        print(
            f"[Episodic] Chroma collection '{EPISODIC_COLLECTION_NAME}' ready at {chroma_dir}"
        )
        return collection
    except ImportError:
        print(
            "[Episodic] chromadb not installed – skipping vector collection setup. "
            "Install with: pip install chromadb"
        )
        return None


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_episodic_record(
    topic: str,
    topic_tags: list[str] | None = None,
    strengths: list[str] | None = None,
    weaknesses: list[str] | None = None,
    questions: list[str] | None = None,
    session_id: str | None = None,
    source_message_ids: list[str] | None = None,
    source_turn_indices: list[int] | None = None,
    source_message_timestamps: list[str] | None = None,
    last_message_timestamp: str | None = None,
    topic_context: dict[str, Any] | None = None,
    topic_embedding: list[float] | None = None,
    memory_item_type: str = EPISODIC_LEARNING_EVENT_TYPE,
    db_path: str | Path = DB_PATH,
    chroma_dir: str | Path = CHROMA_DIR,
    repeat_detection_threshold: float | None = DEFAULT_REPEAT_DETECTION_THRESHOLD,
) -> str:
    """
    Insert or update an Episodic record for the given topic.

    If a record with the same topic already exists:
    - strengths / weaknesses / questions lists are **merged** (deduplicated).
    - last_updated is refreshed.

    Returns the episodic_id of the upserted record.
    """
    create_episodic_table(db_path)
    strengths = strengths or []
    weaknesses = weaknesses or []
    questions = questions or []
    topic = normalize_topic_tag(topic)
    normalized_topic_tags = normalize_topic_tags(topic_tags or [topic])
    source_session_ids = [session_id] if session_id else []
    clean_message_ids = _normalize_string_list(source_message_ids)
    clean_turn_indices = _normalize_turn_indices(source_turn_indices)
    clean_message_timestamps = _normalize_string_list(source_message_timestamps)
    topic_contexts = [_normalize_topic_context(topic_context)] if topic_context else []
    topic_embedding_json = (
        json.dumps(topic_embedding) if topic_embedding is not None else None
    )
    normalized_memory_item_type = _normalize_memory_item_type(memory_item_type)
    now = _now_iso()
    persisted_topic = topic

    with get_db_connection(db_path) as conn:
        existing = conn.execute(
            """
            SELECT * FROM episodic_memory
            WHERE topic = ? AND memory_item_type = ?
            """,
            (topic, normalized_memory_item_type),
        ).fetchone()
        if existing is None and repeat_detection_threshold is not None:
            repeated = find_repeated_episodic_record(
                current_topic=topic,
                current_questions=questions,
                db_path=db_path,
                repeat_threshold=repeat_detection_threshold,
            )
            if repeated is not None:
                existing = conn.execute(
                    "SELECT * FROM episodic_memory WHERE episodic_id = ?",
                    (repeated["episodic_id"],),
                ).fetchone()

        if existing:
            episodic_id = existing["episodic_id"]
            persisted_topic = existing["topic"]
            merged_topic_tags = _merge_ordered(
                _json_list(existing["topic_tags"]), normalized_topic_tags
            )
            merged_strengths = _merge_ordered(
                _json_list(existing["strengths"]), strengths
            )
            merged_weaknesses = _merge_ordered(
                _json_list(existing["weaknesses"]), weaknesses
            )
            merged_questions = _merge_ordered(
                _json_list(existing["questions"]), questions
            )
            merged_source_session_ids = _merge_ordered(
                _json_list(existing["source_session_ids"]), source_session_ids
            )
            merged_source_message_ids = _merge_ordered(
                _json_list(existing["source_message_ids"]), clean_message_ids
            )
            merged_source_turn_indices = _merge_ordered(
                _json_list(existing["source_turn_indices"]), clean_turn_indices
            )
            merged_source_message_timestamps = _merge_ordered(
                _json_list(existing["source_message_timestamps"]),
                clean_message_timestamps,
            )
            merged_topic_contexts = _merge_ordered(
                _json_list(existing["topic_contexts"]), topic_contexts
            )
            occurrence_count = int(existing["occurrence_count"] or 0) + 1
            latest_message_timestamp = _latest_iso_timestamp(
                existing["last_message_timestamp"],
                last_message_timestamp,
            )
            conn.execute(
                """
                UPDATE episodic_memory
                SET topic_tags = ?,
                    strengths = ?,
                    weaknesses = ?,
                    questions = ?,
                    source_session_ids = ?,
                    source_message_ids = ?,
                    source_turn_indices = ?,
                    source_message_timestamps = ?,
                    topic_contexts = ?,
                    occurrence_count = ?,
                    memory_item_type = ?,
                    last_message_timestamp = COALESCE(?, last_message_timestamp),
                    topic_embedding = COALESCE(?, topic_embedding),
                    last_updated = ?
                WHERE episodic_id = ?
                """,
                (
                    json.dumps(merged_topic_tags, ensure_ascii=False),
                    json.dumps(merged_strengths, ensure_ascii=False),
                    json.dumps(merged_weaknesses, ensure_ascii=False),
                    json.dumps(merged_questions, ensure_ascii=False),
                    json.dumps(merged_source_session_ids, ensure_ascii=False),
                    json.dumps(merged_source_message_ids, ensure_ascii=False),
                    json.dumps(merged_source_turn_indices, ensure_ascii=False),
                    json.dumps(merged_source_message_timestamps, ensure_ascii=False),
                    json.dumps(merged_topic_contexts, ensure_ascii=False),
                    occurrence_count,
                    normalized_memory_item_type,
                    latest_message_timestamp,
                    topic_embedding_json,
                    now,
                    episodic_id,
                ),
            )
        else:
            episodic_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO episodic_memory
                    (episodic_id, topic, topic_tags, strengths, weaknesses,
                     questions, source_session_ids, source_message_ids,
                     source_turn_indices, source_message_timestamps,
                     topic_contexts, occurrence_count, memory_item_type,
                     last_message_timestamp, topic_embedding, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episodic_id,
                    topic,
                    json.dumps(normalized_topic_tags, ensure_ascii=False),
                    json.dumps(strengths, ensure_ascii=False),
                    json.dumps(weaknesses, ensure_ascii=False),
                    json.dumps(questions, ensure_ascii=False),
                    json.dumps(source_session_ids, ensure_ascii=False),
                    json.dumps(clean_message_ids, ensure_ascii=False),
                    json.dumps(clean_turn_indices, ensure_ascii=False),
                    json.dumps(clean_message_timestamps, ensure_ascii=False),
                    json.dumps(topic_contexts, ensure_ascii=False),
                    1,
                    normalized_memory_item_type,
                    last_message_timestamp,
                    topic_embedding_json,
                    now,
                ),
            )
        conn.commit()

    # Sync to Chroma if an embedding is provided
    if topic_embedding is not None:
        collection = get_episodic_chroma_collection(chroma_dir)
        if collection is not None:
            stored_record = get_episodic_by_id(episodic_id, db_path=db_path) or {}
            collection.upsert(
                ids=[episodic_id],
                embeddings=[topic_embedding],
                documents=[persisted_topic],
                metadatas=[
                    _episodic_chroma_metadata(
                        episodic_id=episodic_id,
                        topic=persisted_topic,
                        topic_tags=stored_record.get(
                            "topic_tags", normalized_topic_tags
                        ),
                        source_session_ids=stored_record.get(
                            "source_session_ids", source_session_ids
                        ),
                        source_message_ids=stored_record.get(
                            "source_message_ids", clean_message_ids
                        ),
                        source_turn_indices=stored_record.get(
                            "source_turn_indices", clean_turn_indices
                        ),
                        last_message_timestamp=stored_record.get(
                            "last_message_timestamp", last_message_timestamp
                        ),
                        occurrence_count=stored_record.get("occurrence_count", 1),
                        memory_item_type=stored_record.get(
                            "memory_item_type", normalized_memory_item_type
                        ),
                        last_updated=now,
                    )
                ],
            )

    return episodic_id


def _episodic_chroma_metadata(
    *,
    episodic_id: str,
    topic: str,
    topic_tags: list[str],
    source_session_ids: list[str],
    source_message_ids: list[str],
    source_turn_indices: list[int],
    last_message_timestamp: str | None,
    occurrence_count: int,
    memory_item_type: str,
    last_updated: str,
) -> dict[str, str | int]:
    values: dict[str, str | int] = {
        "episodic_id": episodic_id,
        "topic": topic,
        "topic_tags": ",".join(topic_tags),
        "source_session_ids": ",".join(source_session_ids),
        "source_message_ids": ",".join(source_message_ids),
        "source_turn_indices": ",".join(str(index) for index in source_turn_indices),
        "last_message_timestamp": last_message_timestamp or "",
        "occurrence_count": occurrence_count,
        "memory_item_type": memory_item_type,
        "last_updated": last_updated,
    }
    return {field: values[field] for field in EPISODIC_CHROMA_METADATA_FIELDS}


def get_episodic_by_topic(
    topic: str, db_path: str | Path = DB_PATH
) -> dict[str, Any] | None:
    """Fetch a single Episodic record by exact topic name."""
    topic = normalize_topic_tag(topic)
    with get_db_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM episodic_memory WHERE topic = ?", (topic,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_dict(row)


def get_episodic_by_id(
    episodic_id: str, db_path: str | Path = DB_PATH
) -> dict[str, Any] | None:
    """Fetch a single Episodic record by primary key."""
    with get_db_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM episodic_memory WHERE episodic_id = ?", (episodic_id,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_dict(row)


def find_repeated_episodic_record(
    current_topic: str,
    current_questions: list[str] | None = None,
    db_path: str | Path = DB_PATH,
    repeat_threshold: float = DEFAULT_REPEAT_DETECTION_THRESHOLD,
) -> dict[str, Any] | None:
    """
    Find an existing Episodic record that appears to cover the current topic.

    The current topic is normalized first, then compared against stored
    Episodic topics, topic tags, and question history. The best match is
    returned only when its score is at or above ``repeat_threshold``.
    """
    create_episodic_table(db_path)
    normalized_topic = normalize_topic_tag(current_topic)
    query_values = _repeat_query_values(normalized_topic, current_questions)
    if not query_values:
        return None

    best_record: dict[str, Any] | None = None
    best_score = 0.0
    best_field = ""
    best_value = ""

    for record in list_all_episodic(db_path=db_path):
        for field, stored_values in _repeat_candidate_values(record):
            for query_value in query_values:
                for stored_value in stored_values:
                    score = _repeat_similarity_score(query_value, stored_value)
                    if score > best_score:
                        best_record = record
                        best_score = score
                        best_field = field
                        best_value = stored_value

    if best_record is None or best_score < repeat_threshold:
        return None

    match = dict(best_record)
    match["repeat_score"] = best_score
    match["repeat_match_field"] = best_field
    match["repeat_match_value"] = best_value
    match["normalized_current_topic"] = normalized_topic
    return match


def search_episodic_by_embedding(
    query_embedding: list[float],
    n_results: int = 3,
    keyword_tokens: Iterable[str] | None = None,
    memory_item_types: Iterable[str] | None = None,
    db_path: str | Path = DB_PATH,
    chroma_dir: str | Path = CHROMA_DIR,
    hybrid_score_config: HybridScoreConfig = DEFAULT_HYBRID_SCORE_CONFIG,
) -> list[dict[str, Any]]:
    """
    Return up to *n_results* Episodic records whose topic embeddings are closest
    to *query_embedding* (cosine similarity via Chroma).

    When keyword_tokens are provided, semantic hits are filtered to records with
    at least one lexical match and ranked by configurable hybrid relevance.

    Chroma performs the vector search, then SQLite hydrates each hit into the
    full Episodic record so callers can use the learner's topic history in
    responses.
    """
    collection = get_episodic_chroma_collection(chroma_dir)
    if collection is None:
        return []

    item_type_filter = _normalize_memory_item_types(memory_item_types)
    tokens = _normalize_keyword_tokens(keyword_tokens)
    query_n_results = n_results
    if item_type_filter or tokens:
        try:
            query_n_results = max(n_results, collection.count())
        except Exception:
            query_n_results = n_results

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=query_n_results,
        include=list(EPISODIC_CHROMA_QUERY_INCLUDE_FIELDS),
    )

    ids = results.get("ids", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]
    documents = results.get("documents", [[]])[0]

    items = []
    for index, episodic_id in enumerate(ids):
        metadata = metadatas[index] or {}
        document = documents[index]
        retrieval_metadata = {
            "source_id": episodic_id,
            "chroma_document_id": episodic_id,
            "chroma_document": document,
            "chroma_metadata": dict(metadata),
            "retrieval_source": "chroma",
            "retrieval_mode": "semantic",
        }
        record = get_episodic_by_id(episodic_id, db_path=db_path)
        distance = float(distances[index])
        if record is None:
            memory_item_type = _normalize_memory_item_type(
                str(metadata.get("memory_item_type", EPISODIC_LEARNING_EVENT_TYPE))
            )
            if item_type_filter and memory_item_type not in item_type_filter:
                continue
            items.append(
                {
                    "episodic_id": episodic_id,
                    "topic": document,
                    "metadata": metadata,
                    "memory_item_type": memory_item_type,
                    "distance": distance,
                    "semantic_score": normalize_semantic_score(distance),
                    **retrieval_metadata,
                }
            )
            continue

        if item_type_filter and record["memory_item_type"] not in item_type_filter:
            continue
        record.update(retrieval_metadata)
        record["distance"] = distance
        record["semantic_score"] = normalize_semantic_score(distance)
        items.append(record)

    if tokens:
        items = _filter_and_rank_episodic_hits_by_keywords(
            items,
            tokens,
            hybrid_score_config=hybrid_score_config,
        )
    return items[:n_results]


def list_all_episodic(
    db_path: str | Path = DB_PATH,
    memory_item_types: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Return all Episodic records sorted by last_updated descending."""
    item_type_filter = _normalize_memory_item_types(memory_item_types)
    with get_db_connection(db_path) as conn:
        if item_type_filter:
            placeholders = ",".join("?" for _ in item_type_filter)
            rows = conn.execute(
                f"""
                SELECT * FROM episodic_memory
                WHERE memory_item_type IN ({placeholders})
                ORDER BY last_updated DESC
                """,
                tuple(item_type_filter),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM episodic_memory ORDER BY last_updated DESC"
            ).fetchall()
        return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["topic_tags"] = json.loads(d["topic_tags"])
    d["strengths"] = json.loads(d["strengths"])
    d["weaknesses"] = json.loads(d["weaknesses"])
    d["questions"] = json.loads(d["questions"])
    d["source_session_ids"] = json.loads(d["source_session_ids"])
    d["source_message_ids"] = json.loads(d["source_message_ids"])
    d["source_turn_indices"] = json.loads(d["source_turn_indices"])
    d["source_message_timestamps"] = json.loads(d["source_message_timestamps"])
    d["topic_contexts"] = json.loads(d["topic_contexts"])
    d["memory_item_type"] = _normalize_memory_item_type(
        d.get("memory_item_type", EPISODIC_LEARNING_EVENT_TYPE)
    )
    if d.get("topic_embedding"):
        d["topic_embedding"] = json.loads(d["topic_embedding"])
    return d


def _json_list(raw_value: Any) -> list[Any]:
    if raw_value in (None, ""):
        return []
    try:
        parsed = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _merge_ordered(existing: Iterable[Any], additions: Iterable[Any]) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    for item in [*existing, *additions]:
        key = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _normalize_turn_indices(source_turn_indices: list[int] | None) -> list[int]:
    if not source_turn_indices:
        return []
    return [
        index
        for index in source_turn_indices
        if isinstance(index, int) and not isinstance(index, bool) and index >= 0
    ]


def _normalize_string_list(values: list[str] | None) -> list[str]:
    if not values:
        return []
    return [clean for value in values if (clean := str(value).strip())]


def _normalize_topic_context(topic_context: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(topic_context, ensure_ascii=False, sort_keys=True))


def _normalize_memory_item_type(memory_item_type: str | None) -> str:
    clean = str(memory_item_type or "").strip().lower().replace("-", "_")
    if clean in EPISODIC_MEMORY_ITEM_TYPES:
        return clean
    return EPISODIC_LEARNING_EVENT_TYPE


def _normalize_memory_item_types(
    memory_item_types: Iterable[str] | None,
) -> tuple[str, ...]:
    if memory_item_types is None:
        return ()

    normalized: list[str] = []
    seen: set[str] = set()
    for item_type in memory_item_types:
        clean = _normalize_memory_item_type(str(item_type))
        if clean in seen:
            continue
        normalized.append(clean)
        seen.add(clean)
    return tuple(normalized)


def _repeat_query_values(
    normalized_topic: str,
    current_questions: list[str] | None,
) -> tuple[str, ...]:
    values = [
        normalized_topic,
        _topic_comparison_text(normalized_topic),
        *_normalize_string_list(current_questions),
    ]
    return tuple(value for value in (_comparison_text(v) for v in values) if value)


def _repeat_candidate_values(record: dict[str, Any]) -> Iterable[tuple[str, tuple[str, ...]]]:
    yield "topic", tuple(
        value
        for value in (
            _comparison_text(str(record.get("topic", ""))),
            _comparison_text(_topic_comparison_text(str(record.get("topic", "")))),
        )
        if value
    )
    yield "topic_tags", tuple(
        value
        for tag in record.get("topic_tags", [])
        for value in (
            _comparison_text(str(tag)),
            _comparison_text(_topic_comparison_text(str(tag))),
        )
        if value
    )
    yield "questions", tuple(
        value
        for question in record.get("questions", [])
        if (value := _comparison_text(str(question)))
    )


def _topic_comparison_text(topic: str) -> str:
    normalized = normalize_topic_tag(topic)
    _category, topic_text = normalized.split(":", 1)
    return topic_text.replace("-", " ")


def _comparison_text(value: str) -> str:
    text = value.strip().lower()
    if ":" in text:
        category, tail = text.split(":", 1)
        if category in TOPIC_TAG_TAXONOMY:
            text = tail
    text = text.replace("-", " ")
    text = re.sub(r"[^a-z0-9#+ ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return TOPIC_ALIASES.get(text, text)


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


def _latest_iso_timestamp(
    existing_timestamp: str | None,
    new_timestamp: str | None,
) -> str | None:
    if not existing_timestamp:
        return new_timestamp
    if not new_timestamp:
        return existing_timestamp
    return max(existing_timestamp, new_timestamp)


def _normalize_keyword_tokens(
    keyword_tokens: Iterable[str] | None,
) -> tuple[str, ...]:
    """Normalize optional keyword tokens while preserving caller order."""
    if keyword_tokens is None:
        return ()

    normalized: list[str] = []
    seen: set[str] = set()
    for token in keyword_tokens:
        clean = " ".join(str(token).strip().lower().split())
        if not clean or clean in seen:
            continue
        normalized.append(clean)
        seen.add(clean)
    return tuple(normalized)


def _filter_and_rank_episodic_hits_by_keywords(
    hits: list[dict[str, Any]],
    keyword_tokens: tuple[str, ...],
    hybrid_score_config: HybridScoreConfig = DEFAULT_HYBRID_SCORE_CONFIG,
) -> list[dict[str, Any]]:
    """Keep Episodic hits with lexical matches and rank by hybrid relevance."""
    ranked_hits: list[dict[str, Any]] = []
    for hit in hits:
        score, matches = _score_episodic_hit_keywords(hit, keyword_tokens)
        if score == 0:
            continue
        ranked = dict(hit)
        ranked["keyword_score"] = score
        ranked["keyword_matches"] = matches
        ranked["keyword_score_normalized"] = normalize_keyword_match_score(
            matches, keyword_tokens
        )
        ranked["semantic_score"] = normalize_semantic_score(hit.get("distance"))
        ranked["hybrid_score"] = calculate_hybrid_score(
            ranked["semantic_score"],
            ranked["keyword_score_normalized"],
            config=hybrid_score_config,
        )
        ranked_hits.append(ranked)

    return sorted(
        ranked_hits,
        key=lambda hit: (
            -hit["hybrid_score"],
            -hit["keyword_score"],
            -hit["semantic_score"],
        ),
    )


def _score_episodic_hit_keywords(
    hit: dict[str, Any],
    keyword_tokens: tuple[str, ...],
) -> tuple[int, list[str]]:
    searchable_text = _episodic_hit_searchable_text(hit)
    score = 0
    matches: list[str] = []

    for token in keyword_tokens:
        count = searchable_text.count(token)
        if count:
            score += count
            matches.append(token)

    return score, matches


def _episodic_hit_searchable_text(hit: dict[str, Any]) -> str:
    values: list[str] = [str(hit.get("topic", ""))]
    for field in (
        "topic_tags",
        "strengths",
        "weaknesses",
        "questions",
        "source_session_ids",
        "source_turn_indices",
        "memory_item_type",
    ):
        field_value = hit.get(field, [])
        if isinstance(field_value, list):
            values.extend(str(item) for item in field_value)
        elif field_value:
            values.append(str(field_value))
    return " ".join(values).lower()


# ---------------------------------------------------------------------------
# Schema initialisation entry-point
# ---------------------------------------------------------------------------


def init_episodic_schema(
    db_path: str | Path = DB_PATH,
    chroma_dir: str | Path = CHROMA_DIR,
) -> None:
    """Create SQLite table and Chroma collection for Episodic memory."""
    create_episodic_table(db_path)
    get_episodic_chroma_collection(chroma_dir)


# ---------------------------------------------------------------------------
# Quick smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile

    print("=== Episodic Schema Smoke Test ===\n")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_db = Path(tmp) / "test_memory.db"
        tmp_chroma = Path(tmp) / "test_chroma"

        # 1. Init schema
        init_episodic_schema(db_path=tmp_db, chroma_dir=tmp_chroma)

        # 2. Insert a new record (no embedding – Chroma optional)
        eid = upsert_episodic_record(
            topic="Python decorators",
            strengths=["understands @property"],
            weaknesses=["confused about functools.wraps"],
            questions=["What does @wraps do?"],
            db_path=tmp_db,
            chroma_dir=tmp_chroma,
        )
        print(f"Inserted episodic_id: {eid}")

        # 3. Fetch & display
        record = get_episodic_by_id(eid, db_path=tmp_db)
        print(f"Fetched record: {json.dumps(record, ensure_ascii=False, indent=2)}")

        # 4. Upsert again – lists should merge
        upsert_episodic_record(
            topic="Python decorators",
            weaknesses=["unsure about stacking decorators"],
            questions=["Can I stack multiple decorators?"],
            db_path=tmp_db,
            chroma_dir=tmp_chroma,
        )
        updated = get_episodic_by_topic("Python decorators", db_path=tmp_db)
        print(f"After merge: {json.dumps(updated, ensure_ascii=False, indent=2)}")

        assert len(updated["weaknesses"]) == 2, "Merge failed for weaknesses"
        assert len(updated["questions"]) == 2, "Merge failed for questions"

        print("\n✅ Episodic schema smoke-test passed.")
