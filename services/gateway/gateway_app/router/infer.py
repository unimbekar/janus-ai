"""Cheap, deterministic requirement inference.

Phase 3 routing may look at the request, but it may not call a model to do it
(docs/model-routing.md §4). Script detection, code fences, token-ish length, and
attachment types are the whole toolbox.

Explicit ``janus.requirements`` are hard filters. Inference fills a separate
preference set used only for ranking, so a guessed script cannot make an
explicitly selected model ineligible.
"""

from __future__ import annotations

import re

from janus_schemas.chat import ChatMessage, RoutingRequirements

_CODE_FENCE = re.compile(r"```")
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_ARABIC = re.compile(r"[\u0600-\u06FF]")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
_CYRILLIC = re.compile(r"[\u0400-\u04FF]")
_NON_LATIN = re.compile(r"[^\u0000-\u024F\u2000-\u206F\s]")
_LONG_CONTEXT_CHARS = 8_000
_IMAGE_TYPES = frozenset({"image_url", "image", "input_image"})


def infer_requirements(messages: list[ChatMessage]) -> RoutingRequirements:
    texts: list[str] = []
    has_image = False
    for message in messages:
        content = message.content
        if isinstance(content, str):
            texts.append(content)
            continue
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            kind = str(part.get("type", ""))
            if kind in _IMAGE_TYPES or "image_url" in part:
                has_image = True
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)

    blob = "\n".join(texts)
    capabilities: list[str] = []
    languages: list[str] = []
    min_context: int | None = None

    if _CODE_FENCE.search(blob):
        capabilities.append("coding")
    if has_image:
        capabilities.append("vision")
    if len(blob) >= _LONG_CONTEXT_CHARS:
        capabilities.append("long_context")
        min_context = 32_000
    if _NON_LATIN.search(blob):
        capabilities.append("multilingual")
    if _DEVANAGARI.search(blob):
        capabilities.append("indic")
        languages.append("hi")
    if _ARABIC.search(blob):
        languages.append("ar")
    if _CJK.search(blob):
        languages.append("zh")
    if _CYRILLIC.search(blob):
        languages.append("ru")

    return RoutingRequirements(
        capabilities=capabilities,
        languages=languages,
        min_context=min_context,
    )


def merge_requirements(
    explicit: RoutingRequirements, inferred: RoutingRequirements
) -> RoutingRequirements:
    """Union of capabilities and languages; explicit min_context wins."""

    return RoutingRequirements(
        capabilities=_unique(explicit.capabilities + inferred.capabilities),
        languages=_unique(explicit.languages + inferred.languages),
        min_context=explicit.min_context or inferred.min_context,
    )


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
