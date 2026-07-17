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


def test_main_saves_summary_on_exit_after_conversation(monkeypatch, capsys, tmp_path):
    inputs = iter(["hello", "exit"])
    monkeypatch.setattr("builtins.input", lambda *_: next(inputs))

    main(llm_client=FakeLLMClient(response="canned reply"), db_path=tmp_path / "test.db")

    captured = capsys.readouterr()
    assert "세션 요약" in captured.out


def test_main_prints_nothing_about_summary_when_exiting_without_conversation(monkeypatch, capsys, tmp_path):
    inputs = iter(["exit"])
    monkeypatch.setattr("builtins.input", lambda *_: next(inputs))

    main(llm_client=FakeLLMClient(response="canned reply"), db_path=tmp_path / "test.db")

    captured = capsys.readouterr()
    assert "세션 요약" not in captured.out


def test_main_prints_episodic_save_after_conversation(monkeypatch, capsys, tmp_path):
    inputs = iter(["hello", "exit"])
    monkeypatch.setattr("builtins.input", lambda *_: next(inputs))

    def fake_generate(messages):
        last_content = messages[-1]["content"] if messages else ""
        if "topics" in last_content:
            return '{"topics": [{"topic": "decorators", "strengths": [], "weaknesses": [], "questions": []}]}'
        if "요약" in last_content:
            return "정상 요약"
        return "ok"

    main(llm_client=FakeLLMClient(response=fake_generate), db_path=tmp_path / "test.db")

    captured = capsys.readouterr()
    assert "학습 이력" in captured.out
    assert "decorators" in captured.out


def test_main_prints_nothing_about_episodic_when_no_topics_extracted(monkeypatch, capsys, tmp_path):
    inputs = iter(["hello", "exit"])
    monkeypatch.setattr("builtins.input", lambda *_: next(inputs))

    def fake_generate(messages):
        last_content = messages[-1]["content"] if messages else ""
        if "topics" in last_content:
            return '{"topics": []}'
        if "요약" in last_content:
            return "정상 요약"
        return "ok"

    main(llm_client=FakeLLMClient(response=fake_generate), db_path=tmp_path / "test.db")

    captured = capsys.readouterr()
    assert "학습 이력" not in captured.out


def test_main_survives_end_session_failure_and_prints_failure_message(monkeypatch, capsys, tmp_path):
    # end_session()이 비어있지 않은 STM을 요약하려면 대화가 최소 한 번은 있어야 한다.
    # 일반 chat()은 성공시키고 end_session()의 요약 호출(마지막 메시지에 요약 지시가 붙음)만
    # 실패시켜야 두 시나리오가 섞이지 않는다.
    inputs = iter(["hello", "exit"])
    monkeypatch.setattr("builtins.input", lambda *_: next(inputs))

    def fail_only_on_summarization(messages):
        last_content = messages[-1]["content"] if messages else ""
        if "요약" in last_content:
            raise RuntimeError("Gemini API 호출 실패: status=500")
        return "ok"

    main(llm_client=FakeLLMClient(response=fail_only_on_summarization), db_path=tmp_path / "test.db")  # must not raise

    captured = capsys.readouterr()
    assert "실패" in captured.out
