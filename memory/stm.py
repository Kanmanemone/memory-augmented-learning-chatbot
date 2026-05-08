"""
STM (Short-Term Memory) — Layer 1 of the 3-tier memory system.

Stores the most recent N messages of the current conversation in SQLite.
Each message is uniquely identified and ordered within a session.

Schema fields:
  id         : UUID primary key
  session_id : Conversation session identifier
  role       : Message sender ('user', 'assistant', 'system')
  content    : Raw message text
  timestamp  : ISO 8601 creation time
  turn_index : Ordinal position within the session (0-based)
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Iterable, List, Optional


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

DDL_STM = """
CREATE TABLE IF NOT EXISTS stm_messages (
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content    TEXT NOT NULL,
    timestamp  TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    UNIQUE(session_id, turn_index)
);
CREATE INDEX IF NOT EXISTS idx_stm_session ON stm_messages(session_id, turn_index);
"""


def init_stm(conn: sqlite3.Connection) -> None:
    """Create the STM table and index if they do not already exist."""
    _migrate_legacy_stm_schema(conn)
    conn.executescript(DDL_STM)
    conn.commit()


def _migrate_legacy_stm_schema(conn: sqlite3.Connection) -> None:
    """Upgrade the pre-Seed STM schema that used stm_id instead of id."""
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='stm_messages'"
    ).fetchone()
    if table is None:
        return

    cols = {row[1] for row in conn.execute("PRAGMA table_info(stm_messages)")}
    if "id" in cols or "stm_id" not in cols:
        return

    conn.executescript(
        """
        ALTER TABLE stm_messages RENAME TO stm_messages_legacy;
        CREATE TABLE stm_messages (
            id         TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role       TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
            content    TEXT NOT NULL,
            timestamp  TEXT NOT NULL,
            turn_index INTEGER NOT NULL,
            UNIQUE(session_id, turn_index)
        );
        INSERT INTO stm_messages (id, session_id, role, content, timestamp, turn_index)
        SELECT stm_id, session_id, role, content, timestamp, turn_index
        FROM stm_messages_legacy;
        DROP TABLE stm_messages_legacy;
        """
    )


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def add_message(
    conn: sqlite3.Connection,
    session_id: str,
    role: str,
    content: str,
    turn_index: int,
    timestamp: Optional[str] = None,
) -> str:
    """
    Insert a single message into STM.

    Returns the generated stm_id (UUID string).
    """
    if role not in ("user", "assistant", "system"):
        raise ValueError(f"Invalid role '{role}'. Must be user/assistant/system.")

    stm_id = str(uuid.uuid4())
    ts = timestamp or datetime.now(timezone.utc).isoformat()

    conn.execute(
        """
        INSERT INTO stm_messages (id, session_id, role, content, timestamp, turn_index)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (stm_id, session_id, role, content, ts, turn_index),
    )
    conn.commit()
    return stm_id


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def get_recent_messages(
    conn: sqlite3.Connection,
    session_id: str,
    n: int = 20,
) -> List[dict]:
    """
    Return the most recent *n* messages for *session_id*, oldest-first.

    Each dict has keys: id, stm_id, session_id, role, content, timestamp, turn_index.
    """
    rows = conn.execute(
        """
        SELECT id, session_id, role, content, timestamp, turn_index
        FROM stm_messages
        WHERE session_id = ?
        ORDER BY turn_index DESC
        LIMIT ?
        """,
        (session_id, n),
    ).fetchall()

    cols = ("id", "session_id", "role", "content", "timestamp", "turn_index")
    # Reverse so the list is chronological (oldest first)
    messages = [dict(zip(cols, row)) for row in reversed(rows)]
    for message in messages:
        message["stm_id"] = message["id"]
    return messages


def get_current_session_context(
    conn: sqlite3.Connection,
    session_id: str,
    n: int = 20,
) -> dict:
    """
    Return a structured temporary context snapshot for the current session.

    STM stores raw turns; this adapter keeps the raw message list available
    while also exposing the latest user/assistant turns and combined text for
    ambiguous follow-up retrieval.
    """
    messages = get_recent_messages(conn, session_id, n=n)
    user_messages = [
        str(message["content"])
        for message in messages
        if message.get("role") == "user"
    ]
    assistant_messages = [
        str(message["content"])
        for message in messages
        if message.get("role") == "assistant"
    ]

    return {
        "session_id": session_id,
        "messages": messages,
        "message_count": len(messages),
        "user_messages": user_messages,
        "assistant_messages": assistant_messages,
        "last_user_message": user_messages[-1] if user_messages else "",
        "last_assistant_message": assistant_messages[-1] if assistant_messages else "",
        "combined_text": "\n".join(
            str(message["content"])
            for message in messages
            if str(message.get("content", "")).strip()
        ),
    }


def get_all_messages(
    conn: sqlite3.Connection,
    session_id: str,
) -> List[dict]:
    """
    Return *all* messages for *session_id* in chronological order.
    Used when building an LTM summary at session end.
    """
    rows = conn.execute(
        """
        SELECT id, session_id, role, content, timestamp, turn_index
        FROM stm_messages
        WHERE session_id = ?
        ORDER BY turn_index ASC
        """,
        (session_id,),
    ).fetchall()

    cols = ("id", "session_id", "role", "content", "timestamp", "turn_index")
    messages = [dict(zip(cols, row)) for row in rows]
    for message in messages:
        message["stm_id"] = message["id"]
    return messages


def get_next_turn_index(conn: sqlite3.Connection, session_id: str) -> int:
    """Return the next available turn_index for a session (0 if new session)."""
    row = conn.execute(
        "SELECT COALESCE(MAX(turn_index) + 1, 0) FROM stm_messages WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return row[0]


def delete_session_messages(conn: sqlite3.Connection, session_id: str) -> int:
    """
    Remove all STM messages for *session_id*.
    Called after a successful LTM consolidation to free short-term storage.

    Returns the number of rows deleted.
    """
    cur = conn.execute(
        "DELETE FROM stm_messages WHERE session_id = ?", (session_id,)
    )
    conn.commit()
    return cur.rowcount


def delete_messages_by_ids(conn: sqlite3.Connection, message_ids: Iterable[str]) -> int:
    """
    Remove specific STM messages by primary key.

    Used by consolidation when only a retained STM window was transferred into
    LTM and older, non-transferred rows should remain available for inspection.
    """
    ids = [str(message_id) for message_id in message_ids if str(message_id).strip()]
    if not ids:
        return 0

    placeholders = ",".join("?" for _ in ids)
    cur = conn.execute(
        f"DELETE FROM stm_messages WHERE id IN ({placeholders})",
        ids,
    )
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

def open_db(db_path: str = "chatbot_memory.db") -> sqlite3.Connection:
    """
    Open (or create) the SQLite database at *db_path* and ensure the STM
    table exists.  Returns the connection.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    init_stm(conn)
    return conn
