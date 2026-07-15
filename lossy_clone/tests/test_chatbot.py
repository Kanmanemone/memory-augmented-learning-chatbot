import os
import sqlite3
from pathlib import Path

from lossy_clone.chatbot import Chatbot
from lossy_clone.llm import FakeLLMClient


def test_chat_returns_fake_llm_response(tmp_path):
    fake = FakeLLMClient(response="canned reply")
    bot = Chatbot(llm_client=fake, db_path=tmp_path / "test.db")

    reply = bot.chat("hello")

    assert reply == "canned reply"


def test_chat_passes_accumulated_history_to_llm_on_second_call(tmp_path):
    fake = FakeLLMClient(response="ok")
    bot = Chatbot(llm_client=fake, db_path=tmp_path / "test.db")

    bot.chat("first message")
    bot.chat("second message")

    assert len(fake.received_calls) == 2

    second_call_messages = fake.received_calls[1]
    contents = [m["content"] for m in second_call_messages]

    assert "first message" in contents
    assert "ok" in contents  # assistant reply from the first turn
    assert "second message" in contents


def test_chat_persists_messages_to_stm(tmp_path):
    db_path = tmp_path / "test.db"
    fake = FakeLLMClient(response="stored reply")
    bot = Chatbot(llm_client=fake, db_path=db_path, session_id="fixed-session")

    bot.chat("persisted message")

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT role, content FROM stm_messages WHERE session_id = ? ORDER BY turn_index",
        ("fixed-session",),
    ).fetchall()
    conn.close()

    assert rows == [("user", "persisted message"), ("assistant", "stored reply")]


def test_default_db_path_is_package_relative_not_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    fake = FakeLLMClient(response="ok")
    bot = Chatbot(llm_client=fake)

    import lossy_clone

    expected_dir = Path(lossy_clone.__file__).resolve().parent / "data"
    assert bot.db_path.parent == expected_dir
    assert bot.db_path == Path(lossy_clone.__file__).resolve().parent / "data" / "chatbot.db"


def test_end_session_returns_none_for_empty_session(tmp_path):
    db_path = tmp_path / "test.db"
    fake = FakeLLMClient(response="should not be used")
    bot = Chatbot(llm_client=fake, db_path=db_path, session_id="empty-session")

    result = bot.end_session()

    assert result is None
    assert fake.received_calls == []

    conn = sqlite3.connect(db_path)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ltm'"
    ).fetchall()
    conn.close()
    assert tables == []


def test_end_session_summarizes_full_history_with_instruction_message(tmp_path):
    fake = FakeLLMClient(response="요약: 데코레이터에 대해 이야기함")
    bot = Chatbot(llm_client=fake, db_path=tmp_path / "test.db", session_id="s1")

    bot.chat("first message")
    bot.chat("second message")
    summary = bot.end_session()

    assert summary == "요약: 데코레이터에 대해 이야기함"
    assert len(fake.received_calls) == 3  # chat, chat, end_session

    summarize_call = fake.received_calls[2]
    summarize_contents = [m["content"] for m in summarize_call]

    assert "first message" in summarize_contents
    assert "second message" in summarize_contents
    # STM 전체(user/assistant 4개)에 요약 지시 메시지가 최소 1개 더 포함되어야 한다.
    assert len(summarize_call) > 4


def test_end_session_does_not_leak_instruction_message_into_stm(tmp_path):
    db_path = tmp_path / "test.db"
    fake = FakeLLMClient(response="요약본")
    bot = Chatbot(llm_client=fake, db_path=db_path, session_id="s1")

    bot.chat("hello")
    bot.end_session()

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT role, content FROM stm_messages WHERE session_id = ?", ("s1",)
    ).fetchall()
    conn.close()

    # end_session()의 지시 메시지나 요약 응답이 STM에 새 row로 추가되면 안 된다.
    # chat("hello")가 만든 user/assistant 두 개만 남아 있어야 한다.
    assert rows == [("user", "hello"), ("assistant", "요약본")]


def test_end_session_persists_summary_to_ltm_table(tmp_path):
    db_path = tmp_path / "test.db"
    fake = FakeLLMClient(response="저장될 요약")
    bot = Chatbot(llm_client=fake, db_path=db_path, session_id="s1")

    bot.chat("hello")
    bot.end_session()

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT session_id, summary FROM ltm WHERE session_id = ?", ("s1",)
    ).fetchall()
    conn.close()

    assert rows == [("s1", "저장될 요약")]


def test_end_session_called_twice_creates_two_ltm_rows(tmp_path):
    db_path = tmp_path / "test.db"
    fake = FakeLLMClient(response="반복 요약")
    bot = Chatbot(llm_client=fake, db_path=db_path, session_id="s1")

    bot.chat("hello")
    bot.end_session()
    bot.end_session()

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT summary FROM ltm WHERE session_id = ?", ("s1",)
    ).fetchall()
    conn.close()

    assert len(rows) == 2
