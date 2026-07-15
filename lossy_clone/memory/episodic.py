"""Episodic memory — 세션에서 다룬 주제별 강점/약점/질문을 SQLite에 저장/조회한다.

DB 파일 경로 결정은 이 모듈의 책임이 아니다. 호출자가 넘긴
sqlite3.Connection을 그대로 사용한다.

ADR-007: Episodic은 topic/strengths/weaknesses/questions만 담는 append-only
테이블로 시작한다. 같은 topic이 반복돼도 병합(upsert)하지 않고 매번 새 row로
쌓는다 — taxonomy/confidence/source tracking/occurrence_count/embedding 같은
필드는 3/4단계 몫이다.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone

_DDL_EPISODIC = """
CREATE TABLE IF NOT EXISTS episodic (
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    topic      TEXT NOT NULL,
    strengths  TEXT NOT NULL DEFAULT '[]',
    weaknesses TEXT NOT NULL DEFAULT '[]',
    questions  TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_episodic_session ON episodic(session_id, created_at);
"""


def init_episodic(conn: sqlite3.Connection) -> None:
    """episodic 테이블과 인덱스를 생성한다 (이미 있으면 아무 것도 하지 않음, 멱등)."""
    conn.executescript(_DDL_EPISODIC)
    conn.commit()


def save_episodes(conn: sqlite3.Connection, session_id: str, episodes: list[dict]) -> list[dict]:
    """episodes의 각 항목마다 새 row 하나씩 저장하고, 저장된 row들을 dict 리스트로 반환한다.

    episodes의 각 항목은 {"topic": str, "strengths": list[str],
    "weaknesses": list[str], "questions": list[str]} 형태를 그대로 신뢰한다.
    """
    saved_rows = []
    for episode in episodes:
        row = {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "topic": episode["topic"],
            "strengths": episode.get("strengths", []),
            "weaknesses": episode.get("weaknesses", []),
            "questions": episode.get("questions", []),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        conn.execute(
            "INSERT INTO episodic (id, session_id, topic, strengths, weaknesses, questions, created_at) "
            "VALUES (:id, :session_id, :topic, :strengths, :weaknesses, :questions, :created_at)",
            {
                **row,
                "strengths": json.dumps(row["strengths"], ensure_ascii=False),
                "weaknesses": json.dumps(row["weaknesses"], ensure_ascii=False),
                "questions": json.dumps(row["questions"], ensure_ascii=False),
            },
        )
        saved_rows.append(row)

    if saved_rows:
        conn.commit()

    return saved_rows


def get_episodes_by_session(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """해당 session_id의 episodic row를 created_at 오름차순으로 전부 반환한다."""
    rows = conn.execute(
        "SELECT id, session_id, topic, strengths, weaknesses, questions, created_at "
        "FROM episodic WHERE session_id = ? ORDER BY created_at ASC",
        (session_id,),
    ).fetchall()

    episodes = []
    for row in rows:
        episode = dict(row)
        episode["strengths"] = json.loads(episode["strengths"])
        episode["weaknesses"] = json.loads(episode["weaknesses"])
        episode["questions"] = json.loads(episode["questions"])
        episodes.append(episode)

    return episodes
