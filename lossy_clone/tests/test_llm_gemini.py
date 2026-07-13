import os

import pytest
import requests

from lossy_clone.llm import GeminiLLMClient


class _FakeResponse:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json_data = json_data
        self.text = str(json_data)

    def json(self):
        return self._json_data


def _ok_response(text="hello from gemini"):
    return _FakeResponse(
        200,
        {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]},
    )


def test_generate_builds_correct_request_url_and_payload(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["json"] = json
        return _ok_response()

    monkeypatch.setattr(requests, "post", fake_post)

    client = GeminiLLMClient(api_key="test-key", model="gemini-test-model")
    client.generate([{"role": "user", "content": "hi"}])

    assert "gemini-test-model:generateContent" in captured["url"]
    assert "key=test-key" in captured["url"]
    assert captured["json"]["contents"][0]["role"] == "user"
    assert captured["json"]["contents"][0]["parts"][0]["text"] == "hi"


def test_generate_maps_assistant_role_to_model(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, **kwargs):
        captured["json"] = json
        return _ok_response()

    monkeypatch.setattr(requests, "post", fake_post)

    client = GeminiLLMClient(api_key="test-key")
    client.generate(
        [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ]
    )

    roles = [c["role"] for c in captured["json"]["contents"]]
    assert roles == ["user", "model"]


def test_generate_moves_system_messages_to_system_instruction(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, **kwargs):
        captured["json"] = json
        return _ok_response()

    monkeypatch.setattr(requests, "post", fake_post)

    client = GeminiLLMClient(api_key="test-key")
    client.generate(
        [
            {"role": "system", "content": "be nice"},
            {"role": "user", "content": "hi"},
        ]
    )

    assert captured["json"]["systemInstruction"]["parts"][0]["text"] == "be nice"
    assert all(c["role"] != "system" for c in captured["json"]["contents"])


def test_generate_returns_text_from_normal_response(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _ok_response("the answer"))

    client = GeminiLLMClient(api_key="test-key")
    reply = client.generate([{"role": "user", "content": "hi"}])

    assert reply == "the answer"


def test_generate_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(500, {"error": "boom"}))

    client = GeminiLLMClient(api_key="test-key")
    with pytest.raises(RuntimeError):
        client.generate([{"role": "user", "content": "hi"}])


def test_generate_raises_on_unexpected_response_shape(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(200, {"unexpected": True}))

    client = GeminiLLMClient(api_key="test-key")
    with pytest.raises(RuntimeError):
        client.generate([{"role": "user", "content": "hi"}])


def test_generate_raises_when_api_key_missing():
    client = GeminiLLMClient(api_key="")
    with pytest.raises(RuntimeError):
        client.generate([{"role": "user", "content": "hi"}])


def test_constructor_does_not_raise_without_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    GeminiLLMClient()


def test_default_model_used_when_not_specified(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    captured = {}

    def fake_post(url, json=None, timeout=None, **kwargs):
        captured["url"] = url
        return _ok_response()

    monkeypatch.setattr(requests, "post", fake_post)

    client = GeminiLLMClient(api_key="test-key")
    client.generate([{"role": "user", "content": "hi"}])

    assert "models/" in captured["url"]
    assert ":generateContent" in captured["url"]


@pytest.mark.skipif(
    not (os.environ.get("GEMINI_API_KEY") and os.environ.get("RUN_LLM_INTEGRATION") == "1"),
    reason="GEMINI_API_KEY와 RUN_LLM_INTEGRATION=1이 모두 설정된 경우에만 실제 Gemini API를 호출합니다.",
)
def test_generate_real_gemini_api_call():
    client = GeminiLLMClient()
    reply = client.generate([{"role": "user", "content": "Say hello in one short sentence."}])

    assert isinstance(reply, str)
    assert len(reply) > 0
