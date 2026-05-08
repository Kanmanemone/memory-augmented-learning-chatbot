"""
LTM (Long-Term Memory) Schema and Operations

LTM stores session-level summaries with learning analytics:
- id: UUID primary key
- session_id: associated session identifier
- summary: natural language summary of the session
- struggles: JSON array of things the user struggled with
- strengths: JSON array of things the user did well
- confusions: JSON array of things the user was confused about
- topic_tags: JSON array of normalized generated topic tags associated with the session
- embedding: vector embedding stored in ChromaDB
- created_at: ISO 8601 timestamp
"""

import sqlite3
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import chromadb
from chromadb.config import Settings

from memory.retrieval import (
    DEFAULT_HYBRID_SCORE_CONFIG,
    HybridScoreConfig,
    calculate_hybrid_score,
    normalize_keyword_match_score,
    normalize_semantic_score,
)

from episodic_schema import normalize_topic_tags


# ──────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────

DB_PATH = Path(__file__).parent.parent / "data" / "chatbot.db"
CHROMA_PATH = Path(__file__).parent.parent / "data" / "chroma"
LTM_COLLECTION_NAME = "ltm_embeddings"
LTM_CHROMA_METADATA_FIELDS = (
    "session_id",
    "struggles",
    "strengths",
    "confusions",
    "topic_tags",
    "created_at",
)
LTM_CHROMA_QUERY_INCLUDE_FIELDS = (
    "documents",
    "metadatas",
    "distances",
)
LTM_CHROMA_QUERY_RESULT_FIELDS = (
    "ids",
    "documents",
    "metadatas",
    "distances",
)


DDL_LTM = """
CREATE TABLE IF NOT EXISTS ltm (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    summary     TEXT NOT NULL,
    struggles   TEXT NOT NULL DEFAULT '[]',
    strengths   TEXT NOT NULL DEFAULT '[]',
    confusions  TEXT NOT NULL DEFAULT '[]',
    topic_tags  TEXT NOT NULL DEFAULT '[]',
    embedding   TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ltm_session_id ON ltm(session_id);
CREATE INDEX IF NOT EXISTS idx_ltm_created_at ON ltm(created_at);
"""


@dataclass(frozen=True)
class LTMSummaryInput:
    """Input contract for persisting one session summary into LTM."""

    session_id: str
    summary: str
    struggles: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    confusions: list[str] = field(default_factory=list)
    topic_tags: list[str] = field(default_factory=list)
    embedding: Optional[list[float]] = None

    def __post_init__(self) -> None:
        if not str(self.session_id).strip():
            raise ValueError("session_id is required for LTM summary input")
        if not str(self.summary).strip():
            raise ValueError("summary is required for LTM summary input")

        object.__setattr__(self, "session_id", str(self.session_id).strip())
        object.__setattr__(self, "summary", str(self.summary).strip())
        object.__setattr__(self, "struggles", _clean_string_list(self.struggles))
        object.__setattr__(self, "strengths", _clean_string_list(self.strengths))
        object.__setattr__(self, "confusions", _clean_string_list(self.confusions))
        object.__setattr__(
            self,
            "topic_tags",
            normalize_topic_tags(_clean_string_list(self.topic_tags)),
        )
        if self.embedding is not None:
            object.__setattr__(
                self, "embedding", [float(value) for value in self.embedding]
            )


def _get_db_connection(db_path: Optional[str | Path] = None) -> sqlite3.Connection:
    """Return a SQLite connection with row_factory set."""
    resolved_db_path = Path(db_path) if db_path is not None else DB_PATH
    resolved_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(resolved_db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _get_chroma_collection(
    chroma_path: Optional[str | Path] = None,
) -> chromadb.Collection:
    """Return (or create) the ChromaDB collection for LTM embeddings."""
    resolved_chroma_path = Path(chroma_path) if chroma_path is not None else CHROMA_PATH
    resolved_chroma_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(
        path=str(resolved_chroma_path),
        settings=Settings(anonymized_telemetry=False),
    )
    collection = client.get_or_create_collection(
        name=LTM_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    return collection


# ──────────────────────────────────────────
# Schema Creation
# ──────────────────────────────────────────

def create_ltm_table(
    conn: Optional[sqlite3.Connection] = None,
    db_path: Optional[str | Path] = None,
) -> None:
    """Create the ltm SQLite table if it doesn't exist."""
    if conn is not None:
        conn.executescript(DDL_LTM)
        _ensure_ltm_topic_tags_column(conn)
        conn.commit()
        return

    resolved_db_path = Path(db_path) if db_path is not None else DB_PATH
    with _get_db_connection(resolved_db_path) as db_conn:
        db_conn.executescript(DDL_LTM)
        _ensure_ltm_topic_tags_column(db_conn)
    print(f"[LTM] SQLite table 'ltm' ready at {resolved_db_path}")


def ensure_ltm_vector_collection(
    chroma_path: Optional[str | Path] = None,
) -> chromadb.Collection:
    """Ensure the ChromaDB LTM collection exists and return it."""
    resolved_chroma_path = Path(chroma_path) if chroma_path is not None else CHROMA_PATH
    col = _get_chroma_collection(resolved_chroma_path)
    print(
        f"[LTM] ChromaDB collection '{LTM_COLLECTION_NAME}' ready at "
        f"{resolved_chroma_path}"
    )
    return col


# ──────────────────────────────────────────
# CRUD Operations
# ──────────────────────────────────────────

def save_ltm(
    session_id: str,
    summary: str,
    struggles: list[str],
    strengths: list[str],
    confusions: list[str],
    embedding: Optional[list[float]] = None,
    topic_tags: Optional[list[str]] = None,
    db_path: Optional[str | Path] = None,
    chroma_path: Optional[str | Path] = None,
) -> str:
    """
    Persist an LTM record to SQLite (and optionally to ChromaDB).

    Returns the new ltm_id (UUID).
    """
    return save_ltm_summary(
        LTMSummaryInput(
            session_id=session_id,
            summary=summary,
            struggles=struggles,
            strengths=strengths,
            confusions=confusions,
            embedding=embedding,
            topic_tags=topic_tags or [],
        ),
        db_path=db_path,
        chroma_path=chroma_path,
    )


def save_ltm_summary(
    summary_input: LTMSummaryInput,
    *,
    db_path: Optional[str | Path] = None,
    chroma_path: Optional[str | Path] = None,
) -> str:
    """
    Persist an LTM summary DTO to SQLite and ChromaDB.

    Returns the new ltm_id (UUID).
    """
    ltm_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    mapped = map_ltm_summary_to_schema(
        summary_input,
        ltm_id=ltm_id,
        created_at=now,
    )

    # ── SQLite ──────────────────────────────
    with _get_db_connection(db_path) as conn:
        create_ltm_table(conn)
        conn.execute(
            """
            INSERT INTO ltm (id, session_id, summary, struggles, strengths,
                             confusions, topic_tags, embedding, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mapped["id"],
                mapped["session_id"],
                mapped["summary"],
                mapped["struggles"],
                mapped["strengths"],
                mapped["confusions"],
                mapped["topic_tags"],
                mapped["embedding"],
                mapped["created_at"],
            ),
        )

    # ── ChromaDB (only when embedding is provided) ──────────────────────────
    if summary_input.embedding is not None:
        col = _get_chroma_collection(chroma_path)
        col.add(
            ids=[ltm_id],
            embeddings=[summary_input.embedding],
            documents=[summary_input.summary],
            metadatas=[
                _ltm_chroma_metadata(summary_input, mapped)
            ],
        )

    print(f"[LTM] Saved record id={ltm_id} for session={summary_input.session_id}")
    return ltm_id


def get_ltm_by_session(session_id: str) -> list[dict]:
    """Retrieve all LTM records for a given session, ordered by created_at."""
    with _get_db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM ltm WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_ltm_by_id(ltm_id: str) -> Optional[dict]:
    """Retrieve a single LTM record by primary key."""
    with _get_db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM ltm WHERE id = ?", (ltm_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_ltm_by_id_from_store(
    ltm_id: str,
    db_path: Optional[str | Path] = None,
) -> Optional[dict]:
    """Retrieve a single LTM record by primary key from a configured store."""
    with _get_db_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM ltm WHERE id = ?", (ltm_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def search_ltm_by_embedding(
    query_embedding: list[float],
    n_results: int = 5,
    keyword_tokens: Optional[Iterable[str]] = None,
    hybrid_score_config: HybridScoreConfig = DEFAULT_HYBRID_SCORE_CONFIG,
    db_path: Optional[str | Path] = None,
    chroma_path: Optional[str | Path] = None,
) -> list[dict]:
    """
    Vector-similarity search over LTM records using ChromaDB.

    When keyword_tokens are provided, the semantic hits are filtered to records
    with at least one lexical match and ranked by configurable hybrid relevance.

    Returns a list of dicts with 'id', 'summary', 'distance', and metadata.
    """
    col = _get_chroma_collection(chroma_path)
    results = col.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        include=list(LTM_CHROMA_QUERY_INCLUDE_FIELDS),
    )

    hits = []
    if results and results["ids"]:
        for idx, ltm_id in enumerate(results["ids"][0]):
            raw_metadata = results["metadatas"][0][idx] or {}
            metadata = _deserialize_ltm_metadata(raw_metadata)
            document = results["documents"][0][idx]
            hydrated = get_ltm_by_id_from_store(ltm_id, db_path=db_path)
            if hydrated is not None:
                metadata = {**metadata, **hydrated}
            hits.append(
                {
                    "id": ltm_id,
                    "source_id": ltm_id,
                    "chroma_document_id": ltm_id,
                    "chroma_document": document,
                    "chroma_metadata": _deserialize_ltm_metadata(raw_metadata),
                    "retrieval_source": "chroma",
                    "retrieval_mode": "semantic",
                    "summary": metadata.pop("summary", document),
                    "distance": results["distances"][0][idx],
                    "semantic_score": normalize_semantic_score(
                        results["distances"][0][idx]
                    ),
                    **metadata,
                }
            )
    tokens = _normalize_keyword_tokens(keyword_tokens)
    if tokens:
        hits = _filter_and_rank_ltm_hits_by_keywords(
            hits,
            tokens,
            hybrid_score_config=hybrid_score_config,
        )
    return hits


def get_all_ltm(
    limit: int = 100,
    db_path: Optional[str | Path] = None,
) -> list[dict]:
    """Return all LTM records (newest first), limited to `limit` rows."""
    with _get_db_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM ltm ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


# ──────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────

def map_ltm_summary_to_schema(
    summary_input: LTMSummaryInput,
    *,
    ltm_id: Optional[str] = None,
    created_at: Optional[str] = None,
) -> dict:
    """Map an LTM summary DTO to the SQLite row shape."""
    return {
        "id": ltm_id or str(uuid.uuid4()),
        "session_id": summary_input.session_id,
        "summary": summary_input.summary,
        "struggles": json.dumps(summary_input.struggles, ensure_ascii=False),
        "strengths": json.dumps(summary_input.strengths, ensure_ascii=False),
        "confusions": json.dumps(summary_input.confusions, ensure_ascii=False),
        "topic_tags": json.dumps(summary_input.topic_tags, ensure_ascii=False),
        "embedding": (
            json.dumps(summary_input.embedding)
            if summary_input.embedding is not None
            else None
        ),
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
    }


def _ltm_chroma_metadata(
    summary_input: LTMSummaryInput,
    mapped: dict,
) -> dict:
    values = {
        "session_id": summary_input.session_id,
        "struggles": mapped["struggles"],
        "strengths": mapped["strengths"],
        "confusions": mapped["confusions"],
        "topic_tags": mapped["topic_tags"],
        "created_at": mapped["created_at"],
    }
    return {field: values[field] for field in LTM_CHROMA_METADATA_FIELDS}


def _clean_string_list(values: Iterable[str]) -> list[str]:
    """Return non-empty stripped strings while preserving order."""
    cleaned: list[str] = []
    for value in values:
        text = str(value).strip()
        if text:
            cleaned.append(text)
    return cleaned


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict, deserialising JSON fields."""
    d = dict(row)
    for field in ("struggles", "strengths", "confusions", "topic_tags"):
        if d.get(field):
            d[field] = json.loads(d[field])
    if d.get("embedding"):
        d["embedding"] = json.loads(d["embedding"])
    return d


def _deserialize_ltm_metadata(metadata: dict) -> dict:
    """Return Chroma metadata with JSON-encoded learning fields decoded."""
    decoded = dict(metadata)
    for field in ("struggles", "strengths", "confusions", "topic_tags"):
        value = decoded.get(field)
        if isinstance(value, str):
            decoded[field] = _deserialize_ltm_metadata_list(value)
    return decoded


def _deserialize_ltm_metadata_list(value: str) -> list[str]:
    """Decode JSON or comma-separated list metadata from Chroma."""
    text = value.strip()
    if not text:
        return []

    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return [item.strip() for item in text.split(",") if item.strip()]

    if isinstance(decoded, list):
        return [str(item) for item in decoded if str(item).strip()]
    return [str(decoded)] if str(decoded).strip() else []


def _ensure_ltm_topic_tags_column(conn: sqlite3.Connection) -> None:
    """Backfill topic_tags for databases created before topic tagging existed."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(ltm)")}
    if "topic_tags" not in cols:
        conn.execute("ALTER TABLE ltm ADD COLUMN topic_tags TEXT NOT NULL DEFAULT '[]'")


def _normalize_keyword_tokens(
    keyword_tokens: Optional[Iterable[str]],
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


def _filter_and_rank_ltm_hits_by_keywords(
    hits: list[dict],
    keyword_tokens: tuple[str, ...],
    hybrid_score_config: HybridScoreConfig = DEFAULT_HYBRID_SCORE_CONFIG,
) -> list[dict]:
    """Keep LTM hits with lexical matches and rank by hybrid relevance."""
    ranked_hits: list[dict] = []
    for hit in hits:
        score, matches = _score_ltm_hit_keywords(hit, keyword_tokens)
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


def _score_ltm_hit_keywords(
    hit: dict,
    keyword_tokens: tuple[str, ...],
) -> tuple[int, list[str]]:
    searchable_text = _ltm_hit_searchable_text(hit)
    score = 0
    matches: list[str] = []

    for token in keyword_tokens:
        count = searchable_text.count(token)
        if count:
            score += count
            matches.append(token)

    return score, matches


def _ltm_hit_searchable_text(hit: dict) -> str:
    values: list[str] = [str(hit.get("summary", ""))]
    for field in ("struggles", "strengths", "confusions", "topic_tags"):
        field_value = hit.get(field, [])
        if isinstance(field_value, list):
            values.extend(str(item) for item in field_value)
        elif field_value:
            values.append(str(field_value))
    return " ".join(values).lower()


# ──────────────────────────────────────────
# Initialisation helper (called at startup)
# ──────────────────────────────────────────

def init_ltm(
    conn: Optional[sqlite3.Connection] = None,
    ensure_vector_collection: bool = True,
    db_path: Optional[str | Path] = None,
    chroma_path: Optional[str | Path] = None,
) -> None:
    """Initialise both the SQLite table and the ChromaDB collection."""
    create_ltm_table(conn, db_path=db_path)
    if ensure_vector_collection:
        ensure_ltm_vector_collection(chroma_path=chroma_path)


if __name__ == "__main__":
    init_ltm()
    print("[LTM] Schema initialisation complete.")
