"""
Topic Tagger — LLM-based automatic topic tagging for the 3-tier memory system.

This module exposes a single public function:

    tag_topics(messages: list[dict], *, model: str = ..., max_topics: int = 5) -> list[str]

It takes a conversation (list of {role, content} dicts) and returns a
deduplicated, sorted list of topic-label strings that the LLM identifies
as the main subjects of that conversation.

Prompt design
-------------
A structured system prompt instructs the LLM to:
  1. Read the conversation.
  2. Identify the main learning topics.
  3. Return a JSON array of short topic strings (no explanations).

The output is parsed strictly; if parsing fails the function falls back to an
empty list so callers never receive an exception from a malformed LLM response.

Dependencies
------------
- google-genai (pip install google-genai)
- GEMINI_API_KEY in ~/.env (or pass ``api_key`` explicitly)

Example
-------
>>> from memory.tagger import tag_topics
>>> msgs = [
...     {"role": "user",      "content": "Can you explain Python decorators?"},
...     {"role": "assistant", "content": "Sure! A decorator wraps a function …"},
...     {"role": "user",      "content": "What about functools.wraps?"},
... ]
>>> tag_topics(msgs)
['Python decorators', 'functools.wraps', 'higher-order functions']
"""

from __future__ import annotations

import json
import os
import re
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from episodic_schema import TOPIC_TAG_TAXONOMY, normalize_topic_tag, normalize_topic_tags

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    genai = SimpleNamespace(Client=None)
    genai_types = SimpleNamespace(GenerateContentConfig=None)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_TOPIC_TAGGING_MODEL = "gemini-2.5-flash"
DEFAULT_MODEL = DEFAULT_TOPIC_TAGGING_MODEL
DEFAULT_MAX_TOPICS = 5
DEFAULT_TOPIC_CONFIDENCE_THRESHOLD = 0.6
TOPIC_TAG_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["topics"],
    "properties": {
        "topics": {
            "type": "array",
            "maxItems": 10,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "topic",
                    "category",
                    "confidence",
                    "source_turn_indices",
                ],
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Canonical topic tag, normalized as category:topic-slug.",
                        "minLength": 1,
                    },
                    "category": {
                        "type": "string",
                        "enum": list(TOPIC_TAG_TAXONOMY.keys()),
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "source_turn_indices": {
                        "type": "array",
                        "items": {
                            "type": "integer",
                            "minimum": 0,
                        },
                    },
                },
            },
        },
    },
}

# System prompt — instructs the LLM to output ONLY a JSON array.
SYSTEM_PROMPT = """\
You are a learning analytics assistant.
Your job is to read a conversation between a student and a tutor, and identify
the main learning topics discussed.

Rules:
- Return ONLY a JSON array of short topic strings (2–5 words each).
- Include at most {max_topics} topics, ordered by relevance (most relevant first).
- Do NOT include any explanation, markdown formatting, or extra text.
- If no clear topic is found, return an empty array: []

Example output:
["Python decorators", "functools.wraps", "higher-order functions"]
"""

USER_PROMPT_TEMPLATE = """\
Here is the conversation to analyse:

{conversation}

Return the JSON array of topics now.
"""

STRUCTURED_SYSTEM_PROMPT = """\
You are a learning analytics assistant.
Your job is to read one or more conversation turns between a student and a
tutor, identify the main learning topics, and convert them into normalized
topic-tag candidates for the Episodic memory layer.

Rules:
- Return ONLY a JSON object with a "topics" array.
- Include at most {max_topics} topics, ordered by relevance.
- Each item must include: topic, category, confidence, source_turn_indices.
- category must be one of: {categories}.
- topic should be a short canonical topic name before slug normalization.
- source_turn_indices are zero-based indices from the supplied turns.
- If no clear topic is found, return {{"topics": []}}.

Example output:
{{"topics":[{{"topic":"Python decorators","category":"programming","confidence":0.94,"source_turn_indices":[0,2]}}]}}
"""

STRUCTURED_USER_PROMPT_TEMPLATE = """\
Here are the conversation turns to analyse:

{conversation}

Return the JSON object of normalized topic-tag candidates now.
"""


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _format_conversation(messages: list[dict[str, Any]]) -> str:
    """Render messages as a plain-text dialogue string."""
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown").capitalize()
        content = msg.get("content", "")
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _coerce_messages(
    messages: dict[str, Any] | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Accept a single turn or a list of turns and return a clean list."""
    if isinstance(messages, dict):
        messages = [messages]
    if not isinstance(messages, list):
        return []
    return [msg for msg in messages if isinstance(msg, dict)]


def _extract_json_array(text: str) -> list[str]:
    """
    Parse the LLM's response and extract the first JSON array found.

    Tolerates minor formatting quirks (leading/trailing whitespace, markdown
    code fences, extra text before/after the array).
    """
    # Strip markdown code fences if present
    text = re.sub(r"```(?:json)?", "", text).strip()

    # Try the whole text first
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return [str(t).strip() for t in result if str(t).strip()]
    except json.JSONDecodeError:
        pass

    # Fallback: find the first [...] substring
    match = re.search(r"\[.*?\]", text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return [str(t).strip() for t in result if str(t).strip()]
        except json.JSONDecodeError:
            pass

    logger.warning("tag_topics: could not parse LLM response as JSON array. "
                   "Raw response: %r", text)
    return []


def _strip_json_fences(text: str) -> str:
    return re.sub(r"```(?:json)?", "", text).strip()


def _extract_json_object(text: str) -> dict[str, Any]:
    """
    Parse the first JSON object found in an LLM response.

    The normalized tagging service asks for an object, but this helper tolerates
    markdown fences and explanatory text in the same spirit as the legacy array
    parser.
    """
    text = _strip_json_fences(text)

    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    logger.warning(
        "tag_normalized_topics: could not parse LLM response as JSON object. "
        "Raw response: %r",
        text,
    )
    return {}


def _require_gemini_client() -> Any:
    if getattr(genai, "Client", None) is None:
        raise ImportError(
            "The 'google-genai' package is required for topic tagging. "
            "Install it with: pip install google-genai"
        )
    return genai.Client


def _read_gemini_api_key_from_dotenv(dotenv_path: Path | None = None) -> str | None:
    candidate_paths = [dotenv_path or Path.home() / ".env"]
    project_env_path = Path.cwd() / ".env"
    if project_env_path not in candidate_paths:
        candidate_paths.append(project_env_path)

    for path in candidate_paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.debug("Could not read Gemini API key from %s: %s", path, exc)
            continue

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() == "GEMINI_API_KEY":
                return value.strip().strip("\"'")
    return None


def _resolve_gemini_api_key(api_key: str | None = None) -> str | None:
    return api_key or os.environ.get("GEMINI_API_KEY") or _read_gemini_api_key_from_dotenv()


def _make_generate_content_config(system: str, max_tokens: int) -> Any:
    config_cls = getattr(genai_types, "GenerateContentConfig", None)
    if config_cls is None:
        return {"system_instruction": system, "max_output_tokens": max_tokens}
    return config_cls(system_instruction=system, max_output_tokens=max_tokens)


def _generate_text_with_gemini(
    *,
    model: str,
    system: str,
    user: str,
    api_key: str | None,
    max_tokens: int,
) -> str:
    client_cls = _require_gemini_client()
    client = client_cls(api_key=_resolve_gemini_api_key(api_key))
    response = client.models.generate_content(
        model=model,
        contents=user,
        config=_make_generate_content_config(system, max_tokens),
    )
    text = getattr(response, "text", None)
    return text if isinstance(text, str) else ""


def validate_topic_tag_output(
    payload: dict[str, Any],
    *,
    max_topics: int = DEFAULT_MAX_TOPICS,
    min_confidence: float = DEFAULT_TOPIC_CONFIDENCE_THRESHOLD,
) -> list[dict[str, Any]]:
    """
    Validate and normalize structured LLM output for conversation-turn tagging.

    Expected input shape:
        {"topics": [
            {
                "topic": "Python decorators",
                "category": "programming",
                "confidence": 0.92,
                "source_turn_indices": [0, 2]
            }
        ]}

    Invalid topic items are dropped. Returned items are deduplicated by their
    canonical ``category:topic-slug`` tag while preserving relevance order.
    """
    if not isinstance(payload, dict):
        return []

    raw_topics = payload.get("topics")
    if not isinstance(raw_topics, list):
        return []

    max_topics = max(1, min(max_topics, 10))
    min_confidence = max(0.0, min(float(min_confidence), 1.0))
    validated: list[dict[str, Any]] = []
    index_by_topic: dict[str, int] = {}

    for item in raw_topics:
        if not isinstance(item, dict):
            continue

        raw_topic = item.get("topic")
        category = item.get("category")
        confidence = item.get("confidence")
        source_turn_indices = item.get("source_turn_indices")

        if not isinstance(raw_topic, str) or not raw_topic.strip():
            continue
        if not isinstance(category, str) or category not in TOPIC_TAG_TAXONOMY:
            continue
        if not isinstance(confidence, int | float) or isinstance(confidence, bool):
            continue
        if not 0.0 <= float(confidence) <= 1.0:
            continue
        if float(confidence) < min_confidence:
            continue
        if not isinstance(source_turn_indices, list):
            continue
        if not all(
            isinstance(index, int) and not isinstance(index, bool) and index >= 0
            for index in source_turn_indices
        ):
            continue

        canonical_topic = normalize_topic_tag(raw_topic, category=category)
        candidate = {
            "topic": canonical_topic,
            "category": canonical_topic.split(":", 1)[0],
            "confidence": float(confidence),
            "source_turn_indices": source_turn_indices,
        }

        existing_index = index_by_topic.get(canonical_topic)
        if existing_index is not None:
            if candidate["confidence"] > validated[existing_index]["confidence"]:
                validated[existing_index] = candidate
            continue

        if len(validated) >= max_topics:
            continue

        index_by_topic[canonical_topic] = len(validated)
        validated.append(candidate)

    return validated


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def tag_topics(
    messages: list[dict[str, Any]],
    *,
    model: str = DEFAULT_MODEL,
    max_topics: int = DEFAULT_MAX_TOPICS,
    api_key: str | None = None,
) -> list[str]:
    """
    Analyse a conversation and return a list of topic labels.

    Parameters
    ----------
    messages : list[dict]
        Conversation messages, each with at least ``role`` and ``content`` keys.
        Supports the same format used by STM (stm_id, session_id, … are ignored).
    model : str
        Gemini model identifier to use for tagging.
    max_topics : int
        Maximum number of topic labels to return (1–10).
    api_key : str | None
        Gemini API key. Defaults to the ``GEMINI_API_KEY`` value in ~/.env.

    Returns
    -------
    list[str]
        Deduplicated, non-empty topic strings (may be empty if none found).

    Raises
    ------
    ImportError
        If the ``google-genai`` package is not installed.
    """
    messages = _coerce_messages(messages)
    if not messages:
        return []

    max_topics = max(1, min(max_topics, 10))
    conversation_text = _format_conversation(messages)

    system = SYSTEM_PROMPT.format(max_topics=max_topics)
    user = USER_PROMPT_TEMPLATE.format(conversation=conversation_text)

    logger.debug("tag_topics: calling %s with %d messages", model, len(messages))

    raw_text = _generate_text_with_gemini(
        model=model,
        system=system,
        user=user,
        api_key=api_key,
        max_tokens=256,
    )
    topics = _extract_json_array(raw_text)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_topics: list[str] = []
    for t in topics:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            unique_topics.append(t)

    logger.debug("tag_topics: extracted topics=%r", unique_topics)
    return unique_topics[:max_topics]


def tag_normalized_topics(
    messages: dict[str, Any] | list[dict[str, Any]],
    *,
    model: str = DEFAULT_MODEL,
    max_topics: int = DEFAULT_MAX_TOPICS,
    min_confidence: float = DEFAULT_TOPIC_CONFIDENCE_THRESHOLD,
    api_key: str | None = None,
) -> list[str]:
    """
    Analyse one or more conversation turns and return normalized topic tags.

    Returned tags use the Episodic canonical ``category:topic-slug`` format,
    for example ``programming:python-decorators``. The service prefers the
    structured LLM contract and falls back to normalizing a legacy JSON array
    response so demos remain robust under lightweight mock models.
    """
    messages = _coerce_messages(messages)
    if not messages:
        return []

    max_topics = max(1, min(max_topics, 10))
    conversation_text = _format_conversation(messages)
    system = STRUCTURED_SYSTEM_PROMPT.format(
        max_topics=max_topics,
        categories=", ".join(TOPIC_TAG_TAXONOMY.keys()),
    )
    user = STRUCTURED_USER_PROMPT_TEMPLATE.format(conversation=conversation_text)

    logger.debug(
        "tag_normalized_topics: calling %s with %d messages", model, len(messages)
    )

    raw_text = _generate_text_with_gemini(
        model=model,
        system=system,
        user=user,
        api_key=api_key,
        max_tokens=512,
    )
    structured_payload = _extract_json_object(raw_text)
    validated = validate_topic_tag_output(
        structured_payload,
        max_topics=max_topics,
        min_confidence=min_confidence,
    )
    if validated:
        return [item["topic"] for item in validated]
    if structured_payload:
        logger.info(
            "tag_normalized_topics: no structured topics met confidence threshold %.2f",
            min_confidence,
        )
        return []

    return normalize_topic_tags(_extract_json_array(raw_text))[:max_topics]


def extract_normalized_topic_from_user_message(
    user_message: str,
    *,
    model: str = DEFAULT_MODEL,
    min_confidence: float = DEFAULT_TOPIC_CONFIDENCE_THRESHOLD,
    api_key: str | None = None,
) -> str:
    """
    Extract the primary normalized topic from the current user message.

    This is intentionally scoped to the latest user turn so retrieval can use a
    precise topic hint before assistant context is available. The returned value
    always uses the Episodic ``category:topic-slug`` format.
    """
    content = str(user_message).strip()
    if not content:
        return normalize_topic_tag("")

    topics = tag_normalized_topics(
        {"role": "user", "content": content},
        model=model,
        max_topics=1,
        min_confidence=min_confidence,
        api_key=api_key,
    )
    return topics[0] if topics else normalize_topic_tag("")


# ──────────────────────────────────────────────────────────────────────────────
# Quick smoke-test (run as: python -m memory.tagger)
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG)

    sample_messages = [
        {"role": "user",      "content": "Can you explain Python decorators?"},
        {"role": "assistant", "content": (
            "Sure! A decorator is a function that wraps another function to "
            "extend its behaviour without modifying it directly."
        )},
        {"role": "user",      "content": "What is functools.wraps and why do I need it?"},
        {"role": "assistant", "content": (
            "functools.wraps copies the metadata (like __name__ and __doc__) "
            "of the wrapped function onto the wrapper, so introspection tools "
            "see the original function's identity."
        )},
        {"role": "user",      "content": "Can I stack multiple decorators on one function?"},
    ]

    print("Input messages:")
    for m in sample_messages:
        print(f"  [{m['role']}] {m['content'][:60]}")

    print("\nCalling tag_topics()…")
    try:
        tags = tag_topics(sample_messages)
        print(f"\nExtracted topics ({len(tags)}):")
        for t in tags:
            print(f"  • {t}")
        sys.exit(0)
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)
