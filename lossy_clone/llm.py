"""교체 가능한 LLM 연동 지점.

LLMClient는 벤더 중립적인 인터페이스다. GeminiLLMClient는 google-genai 같은
벤더 SDK 없이 Gemini REST API를 직접 호출하는 기본 구현체다 (ADR-004).
"""

import os
from abc import ABC, abstractmethod
from typing import Callable, Union

import requests

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
_GEMINI_ENDPOINT_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
)


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


class GeminiLLMClient(LLMClient):
    """google-genai SDK 없이 Gemini REST API(generateContent)를 직접 호출하는 구현체.

    .env 파일 경로는 코드에서 하드코딩하지 않는다. GEMINI_API_KEY/GEMINI_MODEL
    환경변수를 프로세스 환경에 로드하는 것은 실행 환경(쉘, python-dotenv 등)의
    책임이며 이 클래스의 책임이 아니다.
    """

    def __init__(self, api_key: Union[str, None] = None, model: Union[str, None] = None):
        self._api_key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY", "")
        self._model = model if model is not None else (os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL)

    def generate(self, messages: list[dict[str, str]]) -> str:
        if not self._api_key:
            raise RuntimeError(
                "GEMINI_API_KEY가 설정되어 있지 않습니다. 환경변수로 API 키를 전달하세요."
            )

        payload = self._build_payload(messages)
        url = _GEMINI_ENDPOINT_TEMPLATE.format(model=self._model, api_key=self._api_key)

        response = requests.post(url, json=payload, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(
                f"Gemini API 호출 실패: status={response.status_code} body={response.text}"
            )

        data = response.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"예상치 못한 Gemini API 응답 형식입니다: {data}") from exc

    @staticmethod
    def _build_payload(messages: list[dict[str, str]]) -> dict:
        contents = []
        system_texts = []

        for message in messages:
            role = message["role"]
            text = message["content"]

            if role == "system":
                system_texts.append(text)
                continue

            gemini_role = "model" if role == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": [{"text": text}]})

        payload: dict = {"contents": contents}
        if system_texts:
            payload["systemInstruction"] = {"parts": [{"text": "\n".join(system_texts)}]}

        return payload
