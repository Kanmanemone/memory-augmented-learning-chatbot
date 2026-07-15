import json
import os
import sqlite3
from pathlib import Path

import pytest

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
    assert len(fake.received_calls) == 4  # chat, chat, LTM 요약, episodic 추출

    summarize_call = fake.received_calls[2]
    summarize_contents = [m["content"] for m in summarize_call]

    assert "first message" in summarize_contents
    assert "second message" in summarize_contents
    # STM 전체(user/assistant 4개)에 요약 지시 메시지가 최소 1개 더 포함되어야 한다.
    assert len(summarize_call) > 4

    episodic_call = fake.received_calls[3]
    episodic_contents = [m["content"] for m in episodic_call]
    assert "first message" in episodic_contents
    assert "second message" in episodic_contents
    # 요약 호출과 episodic 호출의 지시 메시지는 서로 달라야 한다 (구분 가능해야 함).
    assert summarize_call[0]["content"] != episodic_call[0]["content"]


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


def _make_episodic_fake(episodic_response, summary_response="요약"):
    """summary_response는 LTM 요약 지시(system 메시지에 'topics'가 없음)에,
    episodic_response는 episodic 추출 지시(system 메시지에 'topics'가 있음)에 응답한다.
    둘 다 콜러블이면 messages를 인자로 호출한다."""

    def _generate(messages):
        instruction = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        if "topics" in instruction:
            return episodic_response(messages) if callable(episodic_response) else episodic_response
        if messages and messages[0]["role"] == "system":
            return summary_response(messages) if callable(summary_response) else summary_response
        return "ok"

    return FakeLLMClient(response=_generate)


def test_end_session_saves_multiple_episodic_topics(tmp_path):
    db_path = tmp_path / "test.db"
    episodic_json = (
        '{"topics": ['
        '{"topic": "decorators", "strengths": ["understands @property"], '
        '"weaknesses": ["confused about wraps"], "questions": ["what is @wraps?"]}, '
        '{"topic": "recursion", "strengths": [], "weaknesses": ["base case"], "questions": []}'
        ']}'
    )
    fake = _make_episodic_fake(episodic_json)
    bot = Chatbot(llm_client=fake, db_path=db_path, session_id="s1")

    bot.chat("hello")
    bot.end_session()

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT topic, strengths, weaknesses, questions FROM episodic WHERE session_id = ? ORDER BY created_at",
        ("s1",),
    ).fetchall()
    conn.close()

    assert len(rows) == 2
    assert rows[0][0] == "decorators"
    assert json.loads(rows[0][1]) == ["understands @property"]
    assert rows[1][0] == "recursion"
    assert json.loads(rows[1][2]) == ["base case"]


def test_end_session_strips_code_fence_from_episodic_response(tmp_path):
    db_path = tmp_path / "test.db"
    fenced = '```json\n{"topics": [{"topic": "decorators", "strengths": [], "weaknesses": [], "questions": []}]}\n```'
    fake = _make_episodic_fake(fenced)
    bot = Chatbot(llm_client=fake, db_path=db_path, session_id="s1")

    bot.chat("hello")
    bot.end_session()

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT topic FROM episodic WHERE session_id = ?", ("s1",)
    ).fetchall()
    conn.close()

    assert rows == [("decorators",)]


def test_end_session_with_empty_topics_saves_no_episodic_rows(tmp_path):
    db_path = tmp_path / "test.db"
    fake = _make_episodic_fake('{"topics": []}', summary_response="정상 요약")
    bot = Chatbot(llm_client=fake, db_path=db_path, session_id="s1")

    bot.chat("hello")
    summary = bot.end_session()

    assert summary == "정상 요약"

    conn = sqlite3.connect(db_path)
    episodic_rows = conn.execute(
        "SELECT * FROM episodic WHERE session_id = ?", ("s1",)
    ).fetchall()
    ltm_rows = conn.execute(
        "SELECT summary FROM ltm WHERE session_id = ?", ("s1",)
    ).fetchall()
    conn.close()

    assert episodic_rows == []
    assert ltm_rows == [("정상 요약",)]


def test_end_session_with_malformed_episodic_json_still_saves_summary(tmp_path):
    db_path = tmp_path / "test.db"
    fake = _make_episodic_fake("not json at all", summary_response="정상 요약")
    bot = Chatbot(llm_client=fake, db_path=db_path, session_id="s1")

    bot.chat("hello")
    summary = bot.end_session()  # must not raise

    assert summary == "정상 요약"

    conn = sqlite3.connect(db_path)
    episodic_rows = conn.execute(
        "SELECT * FROM episodic WHERE session_id = ?", ("s1",)
    ).fetchall()
    conn.close()

    assert episodic_rows == []


def test_end_session_skips_topics_with_empty_topic_field(tmp_path):
    db_path = tmp_path / "test.db"
    episodic_json = (
        '{"topics": ['
        '{"topic": "", "strengths": [], "weaknesses": [], "questions": []}, '
        '{"topic": "valid-topic", "strengths": [], "weaknesses": [], "questions": []}'
        ']}'
    )
    fake = _make_episodic_fake(episodic_json)
    bot = Chatbot(llm_client=fake, db_path=db_path, session_id="s1")

    bot.chat("hello")
    bot.end_session()

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT topic FROM episodic WHERE session_id = ?", ("s1",)
    ).fetchall()
    conn.close()

    assert rows == [("valid-topic",)]


def test_end_session_survives_episodic_call_raising_exception(tmp_path):
    db_path = tmp_path / "test.db"

    def raise_episodic(_messages):
        raise RuntimeError("Gemini API 호출 실패: status=500")

    fake = _make_episodic_fake(raise_episodic, summary_response="정상 요약")
    bot = Chatbot(llm_client=fake, db_path=db_path, session_id="s1")

    bot.chat("hello")
    summary = bot.end_session()  # must not raise

    assert summary == "정상 요약"

    conn = sqlite3.connect(db_path)
    ltm_rows = conn.execute(
        "SELECT summary FROM ltm WHERE session_id = ?", ("s1",)
    ).fetchall()
    conn.close()

    assert ltm_rows == [("정상 요약",)]


def test_end_session_skips_episodic_call_when_summary_call_fails(tmp_path):
    db_path = tmp_path / "test.db"

    def raise_summary(messages):
        instruction = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        if "topics" not in instruction and messages and messages[0]["role"] == "system":
            raise RuntimeError("Gemini API 호출 실패: status=500")
        return "ok"

    fake = FakeLLMClient(response=raise_summary)
    bot = Chatbot(llm_client=fake, db_path=db_path, session_id="s1")

    bot.chat("hello")
    with pytest.raises(RuntimeError):
        bot.end_session()

    # chat("hello") 1회 + 요약 시도 1회 = 2. episodic 추출은 시도되지 않아야 한다.
    assert len(fake.received_calls) == 2


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
