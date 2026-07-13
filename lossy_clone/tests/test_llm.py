import pytest

from lossy_clone.llm import FakeLLMClient, LLMClient


def test_llm_client_is_abstract_and_cannot_be_instantiated():
    with pytest.raises(TypeError):
        LLMClient()


def test_fake_llm_client_default_response_reflects_input():
    client = FakeLLMClient()
    reply = client.generate([{"role": "user", "content": "hello there"}])

    assert isinstance(reply, str)
    assert "hello there" in reply


def test_fake_llm_client_with_fixed_response():
    client = FakeLLMClient(response="fixed answer")
    reply = client.generate([{"role": "user", "content": "anything"}])

    assert reply == "fixed answer"


def test_fake_llm_client_with_response_fn():
    def responder(messages):
        return f"saw {len(messages)} messages"

    client = FakeLLMClient(response=responder)
    reply = client.generate([{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}])

    assert reply == "saw 2 messages"


def test_fake_llm_client_records_received_calls():
    client = FakeLLMClient()

    first = [{"role": "user", "content": "first"}]
    second = [{"role": "user", "content": "first"}, {"role": "assistant", "content": "reply"}, {"role": "user", "content": "second"}]

    client.generate(first)
    client.generate(second)

    assert len(client.received_calls) == 2
    assert client.received_calls[0] == first
    assert client.received_calls[1] == second
