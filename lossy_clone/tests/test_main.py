from lossy_clone.__main__ import main
from lossy_clone.llm import FakeLLMClient


def test_main_prints_greeting(monkeypatch, capsys, tmp_path):
    inputs = iter(["exit"])
    monkeypatch.setattr("builtins.input", lambda *_: next(inputs))

    main(llm_client=FakeLLMClient(response="ok"), db_path=tmp_path / "test.db")

    captured = capsys.readouterr()
    assert "lossy_clone" in captured.out.lower()


def test_main_echoes_bot_replies_until_exit_command(monkeypatch, capsys, tmp_path):
    inputs = iter(["hello", "exit"])
    monkeypatch.setattr("builtins.input", lambda *_: next(inputs))

    main(llm_client=FakeLLMClient(response="canned reply"), db_path=tmp_path / "test.db")

    captured = capsys.readouterr()
    assert "canned reply" in captured.out


def test_main_stops_on_eof(monkeypatch, capsys, tmp_path):
    def raise_eof(*_):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)

    main(llm_client=FakeLLMClient(response="ok"), db_path=tmp_path / "test.db")  # must not raise
