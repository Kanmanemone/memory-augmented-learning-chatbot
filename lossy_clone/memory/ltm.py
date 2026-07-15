"""LTM (Long-Term Memory) — 세션 요약을 SQLite에 저장/조회한다.

DB 파일 경로 결정은 이 모듈의 책임이 아니다. 호출자가 넘긴
sqlite3.Connection을 그대로 사용한다.

ADR-005: LTM은 summary 하나만 담는 최소 스키마로 시작한다. struggles/
strengths/confusions/topic_tags/embedding 같은 필드는 3/4단계 몫이다.
"""

import sqlite3
import uuid
from datetime import datetime, timezone

_DDL_LTM = """
CREATE TABLE IF NOT EXISTS ltm (
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    summary    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ltm_session ON ltm(session_id, created_at);
"""


def init_ltm(conn: sqlite3.Connection) -> None:
    """ltm 테이블과 인덱스를 생성한다 (이미 있으면 아무 것도 하지 않음, 멱등)."""
    conn.executescript(_DDL_LTM)
    conn.commit()


def save_summary(conn: sqlite3.Connection, session_id: str, summary: str) -> dict:
    """세션 요약 한 건을 저장하고 저장된 row를 dict로 반환한다."""
    row = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "summary": summary,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    conn.execute(
        "INSERT INTO ltm (id, session_id, summary, created_at) "
        "VALUES (:id, :session_id, :summary, :created_at)",
        row,
    )
    conn.commit()

    return row


def get_summaries_by_session(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """해당 session_id의 요약을 created_at 오름차순으로 전부 반환한다."""
    rows = conn.execute(
        "SELECT id, session_id, summary, created_at "
        "FROM ltm WHERE session_id = ? ORDER BY created_at ASC",
        (session_id,),
    ).fetchall()

    return [dict(row) for row in rows]
