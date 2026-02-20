"""
Shared prompt safety utilities for defense-in-depth against prompt injection.

Provides:
- ``PromptSafetyPolicy``   -- configurable policy (from manifest ``prompt_safety``)
- ``PromptInjectionError`` -- raised when injection is blocked
- ``sanitize_for_prompt``  -- wrap user-sourced text in XML delimiters
- ``detect_injection``     -- flag / block suspicious patterns in user content
- ``TokenBudget``          -- track and enforce prompt token limits

All services that interpolate user-controlled data into LLM prompts
(memory context engineering, graph extraction, graph search, etc.)
should use these utilities.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from ..exceptions import MongoDBEngineError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PromptInjectionError(MongoDBEngineError):
    """Raised when a prompt injection attempt is detected and the policy is ``block``."""

    def __init__(self, message: str, *, patterns: list[str] | None = None) -> None:
        super().__init__(message)
        self.patterns = patterns or []


# ---------------------------------------------------------------------------
# Policy (manifest-driven configuration)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptSafetyPolicy:
    """Runtime prompt-safety configuration, typically built from the manifest.

    Attributes:
        injection_mode: How to handle detected injection patterns.
            ``"log"``  -- log a warning (default, backward-compatible).
            ``"block"`` -- raise ``PromptInjectionError`` on any match.
            ``"block_high_confidence"`` -- block only high-confidence
            patterns (ChatML tokens, Llama markers) while logging the rest.
        max_input_length: Maximum allowed character length for user input.
            ``0`` means no limit (default).
        custom_blocked_patterns: Additional regex patterns to treat as
            injection attempts.  Compiled at construction time.
        allow_patterns: Regex patterns to whitelist (skip injection check
            for matching text).
    """

    injection_mode: str = "log"
    max_input_length: int = 0
    custom_blocked_patterns: tuple[str, ...] = ()
    allow_patterns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.injection_mode not in ("log", "block", "block_high_confidence"):
            raise ValueError(
                f"injection_mode must be 'log', 'block', or 'block_high_confidence'; " f"got '{self.injection_mode}'"
            )


def policy_from_manifest(config: dict[str, Any] | None) -> PromptSafetyPolicy:
    """Build a ``PromptSafetyPolicy`` from a manifest ``prompt_safety`` block.

    Returns the default policy when *config* is ``None`` or empty.
    """
    if not config:
        return PromptSafetyPolicy()
    return PromptSafetyPolicy(
        injection_mode=config.get("injection_mode", "log"),
        max_input_length=int(config.get("max_input_length", 0)),
        custom_blocked_patterns=tuple(config.get("custom_blocked_patterns", ())),
        allow_patterns=tuple(config.get("allow_patterns", ())),
    )


# ---------------------------------------------------------------------------
# Default policy (singleton, used when no manifest config is set)
# ---------------------------------------------------------------------------

_DEFAULT_POLICY = PromptSafetyPolicy()

# Currently active per-process policy.  ``configure()`` updates this.
_active_policy: PromptSafetyPolicy = _DEFAULT_POLICY


def configure(policy: PromptSafetyPolicy) -> None:
    """Set the process-wide prompt-safety policy (called during app startup)."""
    global _active_policy  # noqa: PLW0603
    _active_policy = policy
    logger.info(
        "Prompt safety configured",
        extra={
            "injection_mode": policy.injection_mode,
            "max_input_length": policy.max_input_length,
            "custom_patterns": len(policy.custom_blocked_patterns),
        },
    )


def get_policy() -> PromptSafetyPolicy:
    """Return the currently active prompt-safety policy."""
    return _active_policy


# ---------------------------------------------------------------------------
# Injection pattern detection
# ---------------------------------------------------------------------------

#: High-confidence patterns -- almost certainly injection, never legitimate.
_HIGH_CONFIDENCE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"<\|im_start\|>|<\|im_end\|>", re.IGNORECASE),
    re.compile(r"<<SYS>>|<</SYS>>", re.IGNORECASE),
    re.compile(r"\[INST\]|\[/INST\]", re.IGNORECASE),
]

#: All patterns (high + medium confidence).
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?prior\s+(instructions|context)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(a|an)", re.IGNORECASE),
    re.compile(r"system\s*:\s*", re.IGNORECASE),
    re.compile(r"assistant\s*:\s*", re.IGNORECASE),
    re.compile(r"\bDAN\b.*\bjailbreak\b", re.IGNORECASE),
    *_HIGH_CONFIDENCE_PATTERNS,
    re.compile(r"forget\s+(everything|all)", re.IGNORECASE),
    re.compile(r"override\s+(your|the)\s+(instructions|rules|guidelines)", re.IGNORECASE),
]


def detect_injection(
    text: str,
    *,
    policy: PromptSafetyPolicy | None = None,
) -> list[str]:
    """Return a list of matched injection-pattern names found in *text*.

    Behaviour depends on the active ``PromptSafetyPolicy``:

    - ``"log"`` (default) -- log a warning, never block.
    - ``"block"`` -- raise ``PromptInjectionError`` on any match.
    - ``"block_high_confidence"`` -- raise on high-confidence patterns
      (ChatML / Llama markers), log the rest.

    Args:
        text: User-controlled content to scan.
        policy: Override the active policy (mainly for testing).

    Returns:
        List of matched pattern strings (empty if clean).

    Raises:
        PromptInjectionError: When the policy dictates blocking.
    """
    if not text:
        return []

    pol = policy or _active_policy

    # Build the combined pattern list (built-in + custom)
    patterns = list(_INJECTION_PATTERNS)
    for custom in pol.custom_blocked_patterns:
        try:
            patterns.append(re.compile(custom, re.IGNORECASE))
        except re.error as exc:
            logger.warning("Invalid custom injection pattern %r: %s", custom, exc)

    # Check allow-patterns first
    for allow in pol.allow_patterns:
        try:
            if re.search(allow, text, re.IGNORECASE):
                return []
        except re.error:
            pass

    hits: list[str] = []
    high_confidence_hits: list[str] = []
    for pattern in patterns:
        if pattern.search(text):
            hits.append(pattern.pattern)
            if pattern in _HIGH_CONFIDENCE_PATTERNS:
                high_confidence_hits.append(pattern.pattern)

    if not hits:
        return []

    # Log regardless of mode
    preview = text[:120].replace("\n", " ")
    logger.warning(
        "PROMPT_INJECTION_DETECTED",
        extra={
            "pattern_count": len(hits),
            "high_confidence": len(high_confidence_hits),
            "text_preview": preview,
            "mode": pol.injection_mode,
        },
    )

    # Enforce policy
    if pol.injection_mode == "block":
        raise PromptInjectionError(
            f"Prompt injection blocked: {len(hits)} suspicious pattern(s) detected",
            patterns=hits,
        )
    if pol.injection_mode == "block_high_confidence" and high_confidence_hits:
        raise PromptInjectionError(
            f"Prompt injection blocked: {len(high_confidence_hits)} high-confidence " f"pattern(s) detected",
            patterns=high_confidence_hits,
        )

    return hits


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------

#: Control characters that should never appear in prompt content.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def sanitize_for_prompt(
    text: str,
    *,
    tag: str = "user_content",
    detect: bool = True,
    policy: PromptSafetyPolicy | None = None,
) -> str:
    """Sanitize user-sourced text for safe interpolation into an LLM prompt.

    1. Enforces ``max_input_length`` (if set in the active policy)
    2. Strips control characters (null bytes, BEL, etc.)
    3. Wraps the text in XML delimiters: ``<tag>…</tag>``
    4. Optionally runs injection-pattern detection

    Args:
        text: Raw user-controlled content.
        tag: XML tag name for the delimiter (default ``"user_content"``).
        detect: Whether to run ``detect_injection`` (default ``True``).
        policy: Override the active policy (mainly for testing).

    Returns:
        Sanitized text wrapped in XML delimiters.

    Raises:
        PromptInjectionError: When the policy dictates blocking and
            injection is detected.
        ValueError: When text exceeds ``max_input_length``.
    """
    if not text:
        return ""

    pol = policy or _active_policy

    # Enforce max input length
    if pol.max_input_length > 0 and len(text) > pol.max_input_length:
        raise ValueError(f"Input length ({len(text)}) exceeds maximum allowed " f"({pol.max_input_length})")

    # Strip dangerous control characters
    cleaned = _CONTROL_CHAR_RE.sub("", text)

    # Injection detection (may raise in block mode)
    if detect:
        detect_injection(cleaned, policy=pol)

    return f"<{tag}>{cleaned}</{tag}>"


# ---------------------------------------------------------------------------
# Token Budget
# ---------------------------------------------------------------------------


class PromptTier(IntEnum):
    """Priority tiers for prompt content.  Lower number = higher priority."""

    SYSTEM = 1  # System instructions, persona (never cut)
    STM = 2  # Recent chat history
    LTM = 3  # Long-term memories
    GRAPH = 4  # Graph context
    SUPPLEMENTARY = 5  # Reflections, procedural, etc.


@dataclass
class _BudgetSection:
    tier: PromptTier
    label: str
    text: str
    tokens: int


@dataclass
class TokenBudget:
    """Tracks remaining tokens and enforces a per-prompt budget.

    Usage::

        budget = TokenBudget(max_tokens=12000, model="gpt-4o")
        budget.add(PromptTier.SYSTEM, "instructions", system_text)
        budget.add(PromptTier.LTM, "memories", ltm_text)
        prompt = budget.build()  # truncates lowest-priority sections if over budget
    """

    max_tokens: int = 12_000
    model: str = "gpt-4o"
    _sections: list[_BudgetSection] = field(default_factory=list, init=False)
    _encoder: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            import tiktoken

            self._encoder = tiktoken.encoding_for_model(self.model)
        except (ImportError, KeyError):
            self._encoder = None
            logger.debug("tiktoken not available or model unknown; " "using word-based estimation for token budget")

    def count_tokens(self, text: str) -> int:
        """Return the token count for *text*."""
        if not text:
            return 0
        if self._encoder is not None:
            return len(self._encoder.encode(text))
        # Rough word-based fallback (~1.3 tokens per word)
        return int(len(text.split()) * 1.3)

    def add(self, tier: PromptTier, label: str, text: str) -> None:
        """Add a content section with a priority tier."""
        if not text:
            return
        tokens = self.count_tokens(text)
        self._sections.append(_BudgetSection(tier=tier, label=label, text=text, tokens=tokens))

    @property
    def total_tokens(self) -> int:
        return sum(s.tokens for s in self._sections)

    @property
    def remaining(self) -> int:
        return max(0, self.max_tokens - self.total_tokens)

    def build(self) -> str:
        """Assemble the prompt, truncating lowest-priority sections if over budget.

        Sections are sorted by tier (highest priority first).  When the
        budget is exceeded, the lowest-priority sections are removed
        entirely.  If a single section is too large, it is truncated at
        a word boundary.

        Returns:
            The assembled prompt string.
        """
        # Sort by tier (ascending = highest priority first)
        ordered = sorted(self._sections, key=lambda s: s.tier)

        used = 0
        parts: list[str] = []
        truncated_labels: list[str] = []

        for section in ordered:
            if used + section.tokens <= self.max_tokens:
                parts.append(section.text)
                used += section.tokens
            else:
                remaining = self.max_tokens - used
                if remaining > 50:
                    # Truncate this section to fit
                    truncated = self._truncate_to_tokens(section.text, remaining)
                    parts.append(truncated)
                    used += self.count_tokens(truncated)
                    truncated_labels.append(section.label)
                else:
                    truncated_labels.append(section.label)

        if truncated_labels:
            logger.warning(
                "TOKEN_BUDGET_TRUNCATION",
                extra={
                    "max_tokens": self.max_tokens,
                    "total_before": self.total_tokens,
                    "truncated_sections": truncated_labels,
                },
            )

        return "\n\n".join(parts)

    def _truncate_to_tokens(self, text: str, max_tokens: int) -> str:
        """Truncate *text* to roughly *max_tokens* at a word boundary."""
        if self._encoder is not None:
            tokens = self._encoder.encode(text)
            if len(tokens) <= max_tokens:
                return text
            truncated_tokens = tokens[:max_tokens]
            return self._encoder.decode(truncated_tokens) + "\n[...truncated]"

        # Word-based fallback
        words = text.split()
        limit = int(max_tokens / 1.3)
        if len(words) <= limit:
            return text
        return " ".join(words[:limit]) + "\n[...truncated]"
