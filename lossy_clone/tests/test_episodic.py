import sqlite3

import pytest

from lossy_clone.memory.episodic import get_episodes_by_session, init_episodic, save_episodes


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    init_episodic(connection)
    yield connection
    connection.close()


def test_init_episodic_is_idempotent(conn):
    init_episodic(conn)  # second call must not raise
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='episodic'"
    ).fetchall()
    assert len(tables) == 1


def test_save_episodes_returns_rows_with_expected_fields(conn):
    episodes = [
        {
            "topic": "decorators",
            "strengths": ["understands @property"],
            "weaknesses": ["confused about functools.wraps"],
            "questions": ["What does @wraps do?"],
        },
        {
            "topic": "recursion",
            "strengths": [],
            "weaknesses": ["base case off-by-one"],
            "questions": [],
        },
    ]

    rows = save_episodes(conn, session_id="s1", episodes=episodes)

    assert len(rows) == 2
    assert rows[0]["session_id"] == "s1"
    assert rows[0]["topic"] == "decorators"
    assert rows[0]["strengths"] == ["understands @property"]
    assert rows[0]["weaknesses"] == ["confused about functools.wraps"]
    assert rows[0]["questions"] == ["What does @wraps do?"]
    assert rows[0]["id"]
    assert rows[0]["created_at"]
    assert isinstance(rows[1]["strengths"], list)
    assert rows[1]["strengths"] == []


def test_get_episodes_by_session_returns_ascending_created_at_order(conn):
    save_episodes(
        conn,
        session_id="s1",
        episodes=[
            {"topic": "first", "strengths": [], "weaknesses": [], "questions": []},
            {"topic": "second", "strengths": [], "weaknesses": [], "questions": []},
        ],
    )

    episodes = get_episodes_by_session(conn, session_id="s1")

    assert [e["topic"] for e in episodes] == ["first", "second"]
    assert isinstance(episodes[0]["strengths"], list)


def test_get_episodes_by_session_is_isolated_per_session(conn):
    save_episodes(
        conn,
        session_id="s1",
        episodes=[{"topic": "s1-topic", "strengths": [], "weaknesses": [], "questions": []}],
    )
    save_episodes(
        conn,
        session_id="s2",
        episodes=[{"topic": "s2-topic", "strengths": [], "weaknesses": [], "questions": []}],
    )

    s1_episodes = get_episodes_by_session(conn, session_id="s1")

    assert [e["topic"] for e in s1_episodes] == ["s1-topic"]


def test_save_episodes_with_empty_list_saves_nothing(conn):
    rows = save_episodes(conn, session_id="s1", episodes=[])

    assert rows == []
    assert get_episodes_by_session(conn, session_id="s1") == []
