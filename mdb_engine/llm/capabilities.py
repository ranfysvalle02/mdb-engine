"""Model Capability Registry for the LLM Service.

This is the single source of truth for *what a model can actually do* —
whether it supports reasoning/thinking, whether Google Search grounding
actually surfaces, vision, structured output, and context size.

Why this exists
---------------
Apps kept hardcoding model knowledge (which model thinks, which grounds, what
the context window is) and re-learning the hard way that, for example,
``gemini-flash-latest`` / ``gemini-pro-latest`` do **not** surface Google Search
grounding through the API even though ``gemini-2.5-*`` does. Centralizing this
here lets every app ask the engine instead of guessing.

Resolution order for an arbitrary ``provider/model`` string:

1. Exact canonical id match in the curated registry.
2. Alias match (e.g. ``gemini-flash-latest``).
3. Family heuristic (substring rules) — always returns *something*.

Manifest overrides (``llm_config.model_overrides``) are layered on top so an app
can correct or extend the map without waiting for an engine release.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any

logger = logging.getLogger(__name__)

# Feature names accepted by ``LLMService.supports()`` — maps to a bool field.
SUPPORTED_FEATURES = ("thinking", "web_search", "vision", "structured_output")


@dataclass(frozen=True)
class ModelCapabilities:
    """Declarative description of what a model can do.

    Attributes:
        model: Canonical id in ``provider/model`` form, e.g.
            ``"gemini/gemini-2.5-flash"``.
        family: Coarse family bucket, e.g. ``"gemini-2.5"``, ``"gemini-3"``,
            ``"gemini-latest"``, ``"gpt-4o"``.
        provider: ``"gemini"`` | ``"openai"`` | ``"azure"``.
        thinking: Whether the model accepts ``reasoning_effort`` (thinking).
        default_reasoning: A sensible default effort (``"low"``/``"medium"``/
            ``"high"``) or ``None`` when the model does not think.
        web_search: Whether Google Search grounding *actually surfaces* for this
            model (the important, hard-won bit — ``-latest`` aliases are False).
        vision: Whether the model accepts image input.
        structured_output: Whether the model supports JSON/structured output.
        max_input_tokens: Context window in tokens, or ``None`` if unknown.
        aliases: Other ids that resolve to this entry.
        notes: Free-form caveats.
    """

    model: str
    family: str
    provider: str
    thinking: bool = False
    default_reasoning: str | None = None
    web_search: bool = False
    vision: bool = True
    structured_output: bool = True
    max_input_tokens: int | None = None
    aliases: tuple[str, ...] = ()
    notes: str = ""


# ---------------------------------------------------------------------------
# Curated default registry — versioned with each engine release.
# ---------------------------------------------------------------------------
# Keys are canonical ``provider/model`` ids. The truths here come from live
# verification (see CHANGELOG 0.15.0): gemini-2.5-* ground, the ``-latest``
# aliases do not.
_GEMINI_25_CTX = 1_048_576
_GEMINI_3_CTX = 1_048_576

_DEFAULT_REGISTRY: dict[str, ModelCapabilities] = {
    # --- Gemini 2.5 (grounding verified working) ---------------------------
    "gemini/gemini-2.5-flash": ModelCapabilities(
        model="gemini/gemini-2.5-flash",
        family="gemini-2.5",
        provider="gemini",
        thinking=True,
        default_reasoning="medium",
        web_search=True,
        vision=True,
        structured_output=True,
        max_input_tokens=_GEMINI_25_CTX,
    ),
    "gemini/gemini-2.5-flash-lite": ModelCapabilities(
        model="gemini/gemini-2.5-flash-lite",
        family="gemini-2.5",
        provider="gemini",
        thinking=True,
        default_reasoning="low",
        web_search=True,
        vision=True,
        structured_output=True,
        max_input_tokens=_GEMINI_25_CTX,
    ),
    "gemini/gemini-2.5-pro": ModelCapabilities(
        model="gemini/gemini-2.5-pro",
        family="gemini-2.5",
        provider="gemini",
        thinking=True,
        default_reasoning="high",
        web_search=True,
        vision=True,
        structured_output=True,
        max_input_tokens=_GEMINI_25_CTX,
    ),
    "gemini/gemini-2.0-flash": ModelCapabilities(
        model="gemini/gemini-2.0-flash",
        family="gemini-2.0",
        provider="gemini",
        thinking=False,
        default_reasoning=None,
        web_search=True,
        vision=True,
        structured_output=True,
        max_input_tokens=_GEMINI_25_CTX,
    ),
    # --- Gemini 3 (preview; grounding designed-in but can be flaky) --------
    "gemini/gemini-3-flash-preview": ModelCapabilities(
        model="gemini/gemini-3-flash-preview",
        family="gemini-3",
        provider="gemini",
        thinking=True,
        default_reasoning="medium",
        web_search=True,
        vision=True,
        structured_output=True,
        max_input_tokens=_GEMINI_3_CTX,
        notes="Preview model; grounding availability can rotate with capacity.",
    ),
    "gemini/gemini-3-pro-preview": ModelCapabilities(
        model="gemini/gemini-3-pro-preview",
        family="gemini-3",
        provider="gemini",
        thinking=True,
        default_reasoning="high",
        web_search=True,
        vision=True,
        structured_output=True,
        max_input_tokens=_GEMINI_3_CTX,
        notes="Preview model; grounding availability can rotate with capacity.",
    ),
    # --- Gemini -latest aliases (grounding does NOT surface) ---------------
    "gemini/gemini-flash-latest": ModelCapabilities(
        model="gemini/gemini-flash-latest",
        family="gemini-latest",
        provider="gemini",
        thinking=True,
        default_reasoning="medium",
        web_search=False,
        vision=True,
        structured_output=True,
        max_input_tokens=_GEMINI_25_CTX,
        notes="Verified: Google Search grounding does not surface on this alias.",
    ),
    "gemini/gemini-pro-latest": ModelCapabilities(
        model="gemini/gemini-pro-latest",
        family="gemini-latest",
        provider="gemini",
        thinking=True,
        default_reasoning="high",
        web_search=False,
        vision=True,
        structured_output=True,
        max_input_tokens=_GEMINI_25_CTX,
        notes="Verified: Google Search grounding does not surface on this alias.",
    ),
    # --- OpenAI (no built-in web search wired through LLMService yet) -------
    "openai/gpt-4o": ModelCapabilities(
        model="openai/gpt-4o",
        family="gpt-4o",
        provider="openai",
        thinking=False,
        default_reasoning=None,
        web_search=False,
        vision=True,
        structured_output=True,
        max_input_tokens=128_000,
    ),
    "openai/gpt-4o-mini": ModelCapabilities(
        model="openai/gpt-4o-mini",
        family="gpt-4o",
        provider="openai",
        thinking=False,
        default_reasoning=None,
        web_search=False,
        vision=True,
        structured_output=True,
        max_input_tokens=128_000,
    ),
}

# Per-provider default model to route to when ``grounding_policy="auto"`` needs
# a grounding-capable model. Overridable via ``llm_config.grounding_model``.
_DEFAULT_GROUNDING_MODEL: dict[str, str] = {
    "gemini": "gemini/gemini-2.5-flash",
}


def default_grounding_model(provider: str) -> str | None:
    """Return the curated grounding-capable model for a provider, if any."""
    return _DEFAULT_GROUNDING_MODEL.get(provider)


def list_registry() -> list[ModelCapabilities]:
    """Return all curated registry entries (copies of the frozen dataclasses)."""
    return list(_DEFAULT_REGISTRY.values())


def _provider_of(model: str) -> str:
    lower = model.lower()
    if lower.startswith("azure/"):
        return "azure"
    if lower.startswith("gemini/") or lower.startswith("vertex_ai/"):
        return "gemini"
    return "openai"


def _canonical(model: str) -> str:
    """Normalize a model string to ``provider/model`` form for lookups."""
    if "/" in model:
        provider, _, name = model.partition("/")
        if provider == "vertex_ai":
            provider = "gemini"
        return f"{provider}/{name}"
    # No prefix: infer provider so bare ids (e.g. "gpt-4o") still resolve.
    return f"{_provider_of(model)}/{model}"


def _heuristic_capabilities(model: str) -> ModelCapabilities:
    """Best-effort capabilities for a model not in the curated registry.

    Always returns a value. Critically encodes the rule that Gemini ``-latest``
    aliases do not surface grounding, while ``gemini-2.x``/``gemini-3`` do.
    """
    canonical = _canonical(model)
    provider = _provider_of(canonical)
    name = canonical.split("/", 1)[1].lower()

    if provider == "gemini":
        is_latest_alias = "latest" in name
        if "gemini-2.5" in name or "gemini-2.0" in name:
            family = "gemini-2.5" if "2.5" in name else "gemini-2.0"
        elif "gemini-3" in name:
            family = "gemini-3"
        elif is_latest_alias:
            family = "gemini-latest"
        else:
            family = "gemini"
        is_pro = "pro" in name
        thinking = "gemini-2.0" not in name  # 2.0 flash is non-thinking
        return ModelCapabilities(
            model=canonical,
            family=family,
            provider="gemini",
            thinking=thinking,
            default_reasoning=("high" if is_pro else "medium") if thinking else None,
            # The hard-won rule: -latest aliases don't surface grounding.
            web_search=not is_latest_alias,
            vision=True,
            structured_output=True,
            max_input_tokens=_GEMINI_25_CTX,
            notes="Heuristic capabilities (model not in curated registry)."
            + (" -latest alias: grounding not surfaced." if is_latest_alias else ""),
        )

    if provider in ("openai", "azure"):
        is_reasoning = any(tok in name for tok in ("o1", "o3", "o4", "gpt-5", "gpt-6", "reason"))
        return ModelCapabilities(
            model=canonical,
            family="gpt-4o" if "gpt-4o" in name else (provider if provider == "azure" else "openai"),
            provider=provider,
            thinking=is_reasoning,
            default_reasoning="medium" if is_reasoning else None,
            web_search=False,
            vision="gpt-4" in name or "gpt-5" in name or provider == "azure",
            structured_output=True,
            max_input_tokens=128_000,
            notes="Heuristic capabilities (model not in curated registry).",
        )

    return ModelCapabilities(
        model=canonical,
        family="unknown",
        provider=provider,
        notes="Heuristic capabilities (model not in curated registry).",
    )


def _apply_override(base: ModelCapabilities, override: dict[str, Any]) -> ModelCapabilities:
    """Layer a partial override dict onto a ModelCapabilities (ignoring unknown keys)."""
    valid = {f for f in ModelCapabilities.__dataclass_fields__}  # noqa: C416
    patch = {k: v for k, v in override.items() if k in valid and k != "model"}
    if not patch:
        return base
    return replace(base, **patch)


def resolve_capabilities(
    model: str,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> ModelCapabilities:
    """Resolve capabilities for any model string.

    Args:
        model: A ``provider/model`` (or bare) model id.
        overrides: Optional ``{model_id: {field: value}}`` from
            ``llm_config.model_overrides``. Keys may be canonical ids, bare
            ids, or aliases.

    Returns:
        A ``ModelCapabilities`` — never ``None``.
    """
    canonical = _canonical(model)

    # 1. Exact canonical match.
    caps = _DEFAULT_REGISTRY.get(canonical)

    # 2. Alias match.
    if caps is None:
        for entry in _DEFAULT_REGISTRY.values():
            if model in entry.aliases or canonical in entry.aliases:
                caps = entry
                break

    # 3. Family heuristic.
    if caps is None:
        caps = _heuristic_capabilities(canonical)

    # 4. Manifest overrides (match by canonical, raw, or bare id).
    if overrides:
        for key in (canonical, model, canonical.split("/", 1)[-1]):
            if key in overrides:
                caps = _apply_override(caps, overrides[key])
                break

    return caps


def filter_registry(
    *,
    provider: str | None = None,
    web_search: bool | None = None,
    thinking: bool | None = None,
    vision: bool | None = None,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> list[ModelCapabilities]:
    """Return curated registry entries (with overrides applied) matching filters."""
    out: list[ModelCapabilities] = []
    for entry in _DEFAULT_REGISTRY.values():
        caps = resolve_capabilities(entry.model, overrides)
        if provider is not None and caps.provider != provider:
            continue
        if web_search is not None and caps.web_search != web_search:
            continue
        if thinking is not None and caps.thinking != thinking:
            continue
        if vision is not None and caps.vision != vision:
            continue
        out.append(caps)
    return out
