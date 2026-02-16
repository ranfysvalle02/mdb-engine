"""
Logging utilities for the memory module.

Provides helpers to redact sensitive content (memory text, user queries,
LLM output) so that INFO-level logs never leak PII.
"""


def redact(text: str | None, max_len: int = 0) -> str:
    """Return a safe placeholder for *text* suitable for production logs.

    Args:
        text: The sensitive string to redact.
        max_len: **Ignored** — kept for call-site readability only.
            The function always returns a length-only placeholder.

    Returns:
        ``"<N chars>"`` where *N* is ``len(text)``, or ``"<empty>"``
        when *text* is falsy.

    Examples:
        >>> redact("The user loves pizza")
        '<20 chars>'
        >>> redact("")
        '<empty>'
        >>> redact(None)
        '<empty>'
    """
    if not text:
        return "<empty>"
    return f"<{len(text)} chars>"
