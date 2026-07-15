import sqlite3

import pytest

from lossy_clone.memory.episodic import (
    get_episodes_by_session,
    init_episodic,
    save_episodes,
    search_episodic,
)


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


def test_search_episodic_matches_by_topic(conn):
    save_episodes(
        conn,
        session_id="s1",
        episodes=[{"topic": "decorators", "strengths": [], "weaknesses": [], "questions": []}],
    )

    results = search_episodic(conn, query="decorators 궁금해요")

    assert len(results) == 1
    assert results[0]["topic"] == "decorators"


def test_search_episodic_matches_by_questions_field(conn):
    save_episodes(
        conn,
        session_id="s1",
        episodes=[
            {
                "topic": "erasers",
                "strengths": [],
                "weaknesses": [],
                "questions": ["흑연은 지우개로 지워지는데 왜 볼펜은 지워지지 않나요"],
            }
        ],
    )

    results = search_episodic(conn, query="볼펜 지우개로 지워지나요")

    assert len(results) == 1
    assert results[0]["topic"] == "erasers"


def test_search_episodic_matches_by_strengths_and_weaknesses(conn):
    save_episodes(
        conn,
        session_id="s1",
        episodes=[
            {
                "topic": "recursion",
                "strengths": ["base case 이해함"],
                "weaknesses": ["재귀 깊이 계산에 약함"],
                "questions": [],
            }
        ],
    )

    results = search_episodic(conn, query="재귀 깊이 계산 어려워요")

    assert len(results) == 1
    assert results[0]["topic"] == "recursion"


def test_search_episodic_returns_empty_list_when_no_overlap(conn):
    save_episodes(
        conn,
        session_id="s1",
        episodes=[{"topic": "decorators", "strengths": [], "weaknesses": [], "questions": []}],
    )

    results = search_episodic(conn, query="완전히 무관한 요리 레시피")

    assert results == []


def test_search_episodic_ranks_higher_overlap_first(conn):
    save_episodes(
        conn,
        session_id="s1",
        episodes=[
            {"topic": "python decorators functools", "strengths": [], "weaknesses": [], "questions": []},
            {"topic": "python", "strengths": [], "weaknesses": [], "questions": []},
        ],
    )

    results = search_episodic(conn, query="python decorators functools")

    assert results[0]["topic"] == "python decorators functools"


def test_search_episodic_respects_limit(conn):
    save_episodes(
        conn,
        session_id="s1",
        episodes=[
            {"topic": f"python topic {i}", "strengths": [], "weaknesses": [], "questions": []}
            for i in range(5)
        ],
    )

    results = search_episodic(conn, query="python", limit=2)

    assert len(results) == 2


def test_search_episodic_works_on_uninitialized_connection():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row

    results = search_episodic(connection, query="anything")

    assert results == []
    connection.close()
