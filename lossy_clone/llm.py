"""교체 가능한 LLM 연동 지점.

LLMClient는 벤더 중립적인 인터페이스다. 실제로 네트워크를 호출하는 구현체는
Step 3(llm-gemini-client)에서 추가되는 GeminiLLMClient를 참고하라. 여기서는
인터페이스와 테스트 전용 FakeLLMClient만 다룬다.
"""

from abc import ABC, abstractmethod
from typing import Callable, Union


class LLMClient(ABC):
    """교체 가능한 LLM 연동 지점. 벤더별 구현은 이 인터페이스만 따르면 된다."""

    @abstractmethod
    def generate(self, messages: list[dict[str, str]]) -> str:
        """messages: [{"role": "user"|"assistant"|"system", "content": str}, ...]
        마지막 응답 텍스트를 반환한다."""
        raise NotImplementedError


class FakeLLMClient(LLMClient):
    """테스트 전용 deterministic 구현체. 네트워크를 전혀 사용하지 않는다."""

    def __init__(self, response: Union[str, Callable[[list[dict[str, str]]], str], None] = None):
        self._response = response
        self.received_calls: list[list[dict[str, str]]] = []

    def generate(self, messages: list[dict[str, str]]) -> str:
        self.received_calls.append(messages)

        if isinstance(self._response, str):
            return self._response
        if callable(self._response):
            return self._response(messages)

        last_user_content = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        return f"[fake-llm] echo: {last_user_content}"
