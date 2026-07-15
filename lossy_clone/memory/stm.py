"""STM (Short-Term Memory) — 세션 내 최근 대화 메시지를 SQLite에 저장/조회한다.

DB 파일 경로 결정은 이 모듈의 책임이 아니다. 호출자(Chatbot)가 넘긴
sqlite3.Connection을 그대로 사용한다.
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

_VALID_ROLES = ("user", "assistant", "system")

_DDL_STM = """
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
    """stm_messages 테이블과 인덱스를 생성한다 (이미 있으면 아무 것도 하지 않음, 멱등)."""
    conn.executescript(_DDL_STM)
    conn.commit()


def add_message(conn: sqlite3.Connection, session_id: str, role: str, content: str) -> dict:
    """새 메시지를 저장하고 저장된 row를 dict로 반환한다.

    turn_index는 해당 session_id 내에서 자동으로 다음 순번을 계산한다.
    """
    if role not in _VALID_ROLES:
        raise ValueError(f"invalid role: {role!r} (expected one of {_VALID_ROLES})")

    next_turn_index = conn.execute(
        "SELECT COALESCE(MAX(turn_index) + 1, 0) FROM stm_messages WHERE session_id = ?",
        (session_id,),
    ).fetchone()[0]

    row = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "role": role,
        "content": content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "turn_index": next_turn_index,
    }

    conn.execute(
        "INSERT INTO stm_messages (id, session_id, role, content, timestamp, turn_index) "
        "VALUES (:id, :session_id, :role, :content, :timestamp, :turn_index)",
        row,
    )
    conn.commit()

    return row


def get_recent_messages(
    conn: sqlite3.Connection, session_id: str, limit: Optional[int] = 20
) -> list[dict]:
    """해당 session_id의 메시지를 turn_index 오름차순(대화 순서)으로 반환한다.

    limit이 정수면 최근 limit개만, None이면 세션 전체 이력을 반환한다.
    """
    if limit is None:
        rows = conn.execute(
            "SELECT id, session_id, role, content, timestamp, turn_index "
            "FROM stm_messages WHERE session_id = ? "
            "ORDER BY turn_index ASC",
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    rows = conn.execute(
        "SELECT id, session_id, role, content, timestamp, turn_index "
        "FROM stm_messages WHERE session_id = ? "
        "ORDER BY turn_index DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()

    return [dict(row) for row in reversed(rows)]
