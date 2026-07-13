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
