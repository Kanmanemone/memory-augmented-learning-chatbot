import sqlite3

import pytest

from lossy_clone.memory.ltm import get_summaries_by_session, init_ltm, save_summary


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    init_ltm(connection)
    yield connection
    connection.close()


def test_init_ltm_is_idempotent(conn):
    init_ltm(conn)  # second call must not raise
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ltm'"
    ).fetchall()
    assert len(tables) == 1


def test_save_summary_returns_row_with_expected_fields(conn):
    row = save_summary(conn, session_id="s1", summary="사용자는 데코레이터에 대해 질문했다.")

    assert row["session_id"] == "s1"
    assert row["summary"] == "사용자는 데코레이터에 대해 질문했다."
    assert row["id"]
    assert row["created_at"]


def test_get_summaries_by_session_returns_ascending_created_at_order(conn):
    save_summary(conn, session_id="s1", summary="첫 번째 요약")
    save_summary(conn, session_id="s1", summary="두 번째 요약")
    save_summary(conn, session_id="s1", summary="세 번째 요약")

    summaries = get_summaries_by_session(conn, session_id="s1")

    assert [s["summary"] for s in summaries] == ["첫 번째 요약", "두 번째 요약", "세 번째 요약"]


def test_get_summaries_by_session_is_isolated_per_session(conn):
    save_summary(conn, session_id="s1", summary="s1의 요약")
    save_summary(conn, session_id="s2", summary="s2의 요약")

    s1_summaries = get_summaries_by_session(conn, session_id="s1")

    assert [s["summary"] for s in s1_summaries] == ["s1의 요약"]
