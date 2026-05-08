"""
Retrieval input contracts for long-term and episodic memory.

The retrieval layer uses two complementary signals:
- semantic_query: natural-language text for embedding/vector search
- keyword_tokens: normalized lexical hints for structured or hybrid filtering
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from math import isfinite
from typing import Any, Iterable, Literal


class RetrievalInputValidationError(ValueError):
    """Raised when a memory retrieval input is missing required signals."""


_QUERY_STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "did",
    "do",
    "does",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "prior",
    "show",
    "the",
    "to",
    "was",
    "were",
    "what",
    "when",
    "where",
    "why",
    "with",
}


def _normalize_semantic_query(semantic_query: str) -> str:
    text = semantic_query.strip()
    if not text:
        raise RetrievalInputValidationError("semantic_query must not be empty")
    return text


def extract_keyword_tokens(query: str) -> tuple[str, ...]:
    """
    Extract normalized lexical tokens from a natural-language retrieval query.

    This shared parser keeps LTM and Episodic keyword filtering aligned with
    the same tokenization, lowercasing, stopword removal, and de-duplication.
    """
    text = _normalize_semantic_query(query)
    text = text.lower().replace("_", " ").replace("-", " ")
    raw_tokens = re.findall(r"[\w#+]+", text)

    tokens: list[str] = []
    seen: set[str] = set()
    for raw_token in raw_tokens:
        token = raw_token.strip("#+")
        if not token or token in _QUERY_STOPWORDS or token in seen:
            continue
        tokens.append(token)
        seen.add(token)

    return tuple(tokens)


def _normalize_keyword_tokens(keyword_tokens: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()

    for token in keyword_tokens:
        clean = " ".join(token.strip().lower().split())
        if not clean or clean in seen:
            continue
        normalized.append(clean)
        seen.add(clean)

    if not normalized:
        raise RetrievalInputValidationError(
            "keyword_tokens must include at least one token"
        )

    return tuple(normalized)


def normalize_semantic_score(distance: float | int | None) -> float:
    """
    Convert Chroma cosine distance into a bounded 0..1 semantic score.

    Chroma cosine distance is 0 for identical vectors and approaches 2 for
    opposite vectors, so this keeps semantic scores comparable with keyword
    match scores.
    """
    if distance is None:
        return 0.0

    try:
        numeric_distance = float(distance)
    except (TypeError, ValueError):
        return 0.0

    if not isfinite(numeric_distance):
        return 0.0

    clamped_distance = min(max(numeric_distance, 0.0), 2.0)
    return 1.0 - (clamped_distance / 2.0)


def normalize_keyword_match_score(
    matches: Iterable[str],
    keyword_tokens: Iterable[str],
) -> float:
    """Return the fraction of requested keyword tokens matched, bounded 0..1."""
    normalized_tokens = {_normalize_score_token(token) for token in keyword_tokens}
    normalized_tokens.discard("")
    if not normalized_tokens:
        return 0.0

    normalized_matches = {_normalize_score_token(match) for match in matches}
    normalized_matches.discard("")
    return len(normalized_matches & normalized_tokens) / len(normalized_tokens)


@dataclass(frozen=True)
class HybridScoreConfig:
    """Weights used to combine normalized semantic and keyword scores."""

    semantic_weight: float = 0.3
    keyword_weight: float = 0.7

    def __post_init__(self) -> None:
        semantic_weight = _normalize_weight(self.semantic_weight, "semantic_weight")
        keyword_weight = _normalize_weight(self.keyword_weight, "keyword_weight")
        if semantic_weight + keyword_weight == 0:
            raise RetrievalInputValidationError(
                "hybrid score weights must include at least one positive value"
            )
        object.__setattr__(self, "semantic_weight", semantic_weight)
        object.__setattr__(self, "keyword_weight", keyword_weight)


def calculate_hybrid_score(
    semantic_score: float | int | None,
    keyword_score: float | int | None,
    config: HybridScoreConfig | None = None,
) -> float:
    """Return a bounded weighted average of semantic and keyword scores."""
    active_config = config or DEFAULT_HYBRID_SCORE_CONFIG
    semantic = _normalize_bounded_score(semantic_score)
    keyword = _normalize_bounded_score(keyword_score)
    weight_sum = active_config.semantic_weight + active_config.keyword_weight
    return (
        (semantic * active_config.semantic_weight)
        + (keyword * active_config.keyword_weight)
    ) / weight_sum


def _normalize_weight(value: float | int, field_name: str) -> float:
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        raise RetrievalInputValidationError(
            f"{field_name} must be a finite non-negative number"
        ) from None

    if not isfinite(numeric_value) or numeric_value < 0:
        raise RetrievalInputValidationError(
            f"{field_name} must be a finite non-negative number"
        )
    return numeric_value


def _normalize_bounded_score(value: float | int | None) -> float:
    if value is None:
        return 0.0
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not isfinite(numeric_value):
        return 0.0
    return min(max(numeric_value, 0.0), 1.0)


def _normalize_score_token(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


DEFAULT_HYBRID_SCORE_CONFIG = HybridScoreConfig()
_MEMORY_SOURCE_PRIORITY = {
    "stm": 0,
    "episodic": 1,
    "ltm": 2,
}


def merge_memory_search_results(
    ltm_hits: Iterable[dict[str, Any]],
    episodic_hits: Iterable[dict[str, Any]],
    limit: int | None = None,
    *,
    stm_hits: Iterable[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Merge STM, LTM, and Episodic hits into one relevance-ordered result list.

    Deduplication is source-aware: an LTM row and an Episodic row may share the
    same UUID-like value but still represent different memory layers. Only
    repeated hits with the same (source, source_id) are collapsed. The original
    hit is preserved under ``record`` for callers that need layer-specific
    fields.
    """
    merged_by_source_id: dict[tuple[str, str], dict[str, Any]] = {}

    for source, hits in (
        ("stm", stm_hits or ()),
        ("ltm", ltm_hits),
        ("episodic", episodic_hits),
    ):
        for item in _normalize_memory_search_results_with_position(source, hits):
            source_id = item["source_id"]
            key = (source, source_id)
            existing = merged_by_source_id.get(key)
            if existing is None or _memory_result_sort_key(item) < _memory_result_sort_key(
                existing
            ):
                merged_by_source_id[key] = item

    merged = [
        _without_internal_position(item)
        for item in sorted(merged_by_source_id.values(), key=_memory_result_sort_key)
    ]
    if limit is not None:
        return merged[: max(limit, 0)]
    return merged


def normalize_memory_search_results(
    source: Literal["stm", "ltm", "episodic"],
    hits: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize layer-specific memory hits into the shared retrieval shape."""
    return [
        _without_internal_position(item)
        for item in _normalize_memory_search_results_with_position(source, hits)
    ]


def _normalize_memory_search_results_with_position(
    source: str,
    hits: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for position, hit in enumerate(hits):
        source_id = _memory_hit_source_id(source, hit)
        if source_id is None:
            continue
        normalized.append(
            _normalize_memory_search_hit(source, source_id, hit, position)
        )
    return normalized


def _memory_hit_source_id(source: str, hit: dict[str, Any]) -> str | None:
    if source == "stm":
        source_id = hit.get("stm_id") or hit.get("id")
    elif source == "ltm":
        source_id = hit.get("id") or hit.get("ltm_id")
    else:
        source_id = hit.get("episodic_id") or hit.get("id")

    if source_id is None:
        return None
    source_id_text = str(source_id).strip()
    return source_id_text or None


def _normalize_memory_search_hit(
    source: str,
    source_id: str,
    hit: dict[str, Any],
    position: int,
) -> dict[str, Any]:
    return {
        "source": source,
        "source_id": source_id,
        "content": _memory_hit_content(source, hit),
        "score": _memory_hit_score(hit),
        "metadata": _memory_hit_metadata(source, source_id, hit),
        "record": hit,
        "_position": position,
    }


def _memory_hit_content(source: str, hit: dict[str, Any]) -> str:
    if source == "stm":
        return str(hit.get("content", ""))
    if source == "ltm":
        return str(hit.get("summary", ""))
    return str(hit.get("topic", ""))


def _memory_hit_score(hit: dict[str, Any]) -> float:
    if "turn_index" in hit and "hybrid_score" not in hit and "semantic_score" not in hit:
        return 1.0

    if "hybrid_score" in hit:
        return _normalize_bounded_score(hit.get("hybrid_score"))

    if "semantic_score" in hit:
        return _normalize_bounded_score(hit.get("semantic_score"))

    return normalize_semantic_score(hit.get("distance"))


def _memory_hit_metadata(
    source: str,
    source_id: str,
    hit: dict[str, Any],
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source": source,
        "source_id": source_id,
    }
    source_fields = {
        "stm": ("session_id", "role", "timestamp", "turn_index"),
        "ltm": (
            "session_id",
            "created_at",
            "struggles",
            "strengths",
            "confusions",
            "topic_tags",
        ),
        "episodic": (
            "topic",
            "topic_tags",
            "strengths",
            "weaknesses",
            "questions",
            "last_updated",
            "source_session_ids",
            "source_message_ids",
            "source_turn_indices",
            "source_message_timestamps",
            "memory_item_type",
            "last_message_timestamp",
            "occurrence_count",
        ),
    }[source]
    score_fields = (
        "distance",
        "semantic_score",
        "keyword_score",
        "keyword_matches",
        "keyword_score_normalized",
        "hybrid_score",
        "retrieval_source",
        "retrieval_mode",
        "chroma_document_id",
        "chroma_document",
        "chroma_metadata",
    )
    for field in (*source_fields, *score_fields):
        if field in hit:
            metadata[field] = hit[field]
    return metadata


def _memory_result_sort_key(
    item: dict[str, Any],
) -> tuple[float, float, float, int, str, str]:
    metadata = item.get("metadata", {})
    distance = metadata.get("distance")
    try:
        numeric_distance = float(distance)
    except (TypeError, ValueError):
        numeric_distance = 2.0
    source = str(item.get("source", ""))
    position = int(item.get("_position", 0))
    if source == "stm" and "turn_index" in metadata:
        try:
            position = -int(metadata["turn_index"])
        except (TypeError, ValueError):
            pass
    return (
        -_normalize_bounded_score(item.get("score")),
        float(_MEMORY_SOURCE_PRIORITY.get(source, 99)),
        numeric_distance,
        position,
        source,
        str(item.get("source_id", "")),
    )


def _without_internal_position(item: dict[str, Any]) -> dict[str, Any]:
    public_item = dict(item)
    public_item.pop("_position", None)
    return public_item


@dataclass(frozen=True)
class LTMRetrievalInput:
    """Validated retrieval input for session-level LTM summaries."""

    semantic_query: str
    keyword_tokens: tuple[str, ...]
    memory_layer: Literal["ltm"] = "ltm"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "semantic_query",
            _normalize_semantic_query(self.semantic_query),
        )
        object.__setattr__(
            self,
            "keyword_tokens",
            _normalize_keyword_tokens(self.keyword_tokens),
        )


@dataclass(frozen=True)
class EpisodicRetrievalInput:
    """Validated retrieval input for topic-level Episodic memory."""

    semantic_query: str
    keyword_tokens: tuple[str, ...]
    memory_layer: Literal["episodic"] = "episodic"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "semantic_query",
            _normalize_semantic_query(self.semantic_query),
        )
        object.__setattr__(
            self,
            "keyword_tokens",
            _normalize_keyword_tokens(self.keyword_tokens),
        )


def build_ltm_retrieval_input(
    semantic_query: str,
    keyword_tokens: Iterable[str] | None = None,
) -> LTMRetrievalInput:
    """Build and validate an LTM retrieval input."""
    tokens = (
        extract_keyword_tokens(semantic_query)
        if keyword_tokens is None
        else keyword_tokens
    )
    return LTMRetrievalInput(
        semantic_query=_normalize_semantic_query(semantic_query),
        keyword_tokens=_normalize_keyword_tokens(tokens),
    )


def build_episodic_retrieval_input(
    semantic_query: str,
    keyword_tokens: Iterable[str] | None = None,
) -> EpisodicRetrievalInput:
    """Build and validate an Episodic retrieval input."""
    tokens = (
        extract_keyword_tokens(semantic_query)
        if keyword_tokens is None
        else keyword_tokens
    )
    return EpisodicRetrievalInput(
        semantic_query=_normalize_semantic_query(semantic_query),
        keyword_tokens=_normalize_keyword_tokens(tokens),
    )
