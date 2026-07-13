import sqlite3

import pytest

from lossy_clone.memory.stm import add_message, get_recent_messages, init_stm


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    init_stm(connection)
    yield connection
    connection.close()


def test_init_stm_is_idempotent(conn):
    init_stm(conn)  # second call must not raise
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='stm_messages'"
    ).fetchall()
    assert len(tables) == 1


def test_add_message_returns_row_with_expected_fields(conn):
    row = add_message(conn, session_id="s1", role="user", content="hello")

    assert row["session_id"] == "s1"
    assert row["role"] == "user"
    assert row["content"] == "hello"
    assert row["turn_index"] == 0
    assert row["id"]
    assert row["timestamp"]


def test_turn_index_increments_sequentially_within_session(conn):
    first = add_message(conn, session_id="s1", role="user", content="a")
    second = add_message(conn, session_id="s1", role="assistant", content="b")
    third = add_message(conn, session_id="s1", role="user", content="c")

    assert [first["turn_index"], second["turn_index"], third["turn_index"]] == [0, 1, 2]


def test_turn_index_is_isolated_per_session(conn):
    add_message(conn, session_id="s1", role="user", content="a")
    add_message(conn, session_id="s1", role="assistant", content="b")

    first_in_s2 = add_message(conn, session_id="s2", role="user", content="x")

    assert first_in_s2["turn_index"] == 0


def test_get_recent_messages_returns_conversation_order(conn):
    add_message(conn, session_id="s1", role="user", content="1")
    add_message(conn, session_id="s1", role="assistant", content="2")
    add_message(conn, session_id="s1", role="user", content="3")

    recent = get_recent_messages(conn, session_id="s1", limit=20)

    assert [m["content"] for m in recent] == ["1", "2", "3"]


def test_get_recent_messages_respects_limit_and_returns_most_recent(conn):
    for i in range(5):
        add_message(conn, session_id="s1", role="user", content=str(i))

    recent = get_recent_messages(conn, session_id="s1", limit=2)

    assert [m["content"] for m in recent] == ["3", "4"]


def test_get_recent_messages_handles_fewer_messages_than_limit(conn):
    add_message(conn, session_id="s1", role="user", content="only one")

    recent = get_recent_messages(conn, session_id="s1", limit=20)

    assert [m["content"] for m in recent] == ["only one"]


def test_add_message_rejects_invalid_role(conn):
    with pytest.raises(Exception):
        add_message(conn, session_id="s1", role="bogus", content="x")
