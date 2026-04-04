"""
Write-time computed fields for auto-CRUD collections.

Processes ``x-computed`` schema extensions to derive fields automatically
on document create and update.  Computed values are stored alongside the
document in MongoDB — they are not virtual.

Supported transforms:
    * ``plain_text`` — strip markdown/HTML to approximate plain text
    * ``first_image`` — extract the first image URL from markdown/HTML
    * ``word_count`` — count words, optionally divided by a WPM constant
    * ``truncate`` — plain-text truncation with ellipsis
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import Any

# ── Text transforms ──────────────────────────────────────────────────────

_MD_IMAGE_RE = re.compile(r"!\[.*?\]\((.*?)\)")
_HTML_IMG_RE = re.compile(r'<img\s[^>]*src=["\']([^"\']+)["\']', re.IGNORECASE)


def _strip_markdown(text: str) -> str:
    """Strip common markdown/HTML syntax, returning approximate plain text."""
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"(`{1,3}).*?\1", "", text, flags=re.DOTALL)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"(\*{1,2}|_{1,2})(.*?)\1", r"\2", text)
    text = re.sub(r"^[>\-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _transform_plain_text(value: str, **_kw: Any) -> str:
    return _strip_markdown(value) if value else ""


def _transform_first_image(value: str, **_kw: Any) -> str:
    if not value:
        return ""
    md_match = _MD_IMAGE_RE.search(value)
    if md_match:
        return md_match.group(1)
    html_match = _HTML_IMG_RE.search(value)
    if html_match:
        return html_match.group(1)
    return ""


def _transform_word_count(value: str, *, divide_by: int = 0, **_kw: Any) -> int:
    if not value:
        return 0
    plain = _strip_markdown(value)
    count = len(plain.split())
    if divide_by and divide_by > 0:
        return max(1, math.ceil(count / divide_by))
    return count


def _transform_truncate(value: str, *, max_length: int = 160, **_kw: Any) -> str:
    if not value:
        return ""
    plain = _strip_markdown(value)
    if len(plain) <= max_length:
        return plain
    return plain[: max_length - 1].rsplit(" ", 1)[0] + "\u2026"


_TRANSFORM_REGISTRY: dict[str, Callable[..., Any]] = {
    "plain_text": _transform_plain_text,
    "first_image": _transform_first_image,
    "word_count": _transform_word_count,
    "truncate": _transform_truncate,
}


# ── Schema parsing ───────────────────────────────────────────────────────


def parse_computed_on_write(schema: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Extract ``x-computed`` definitions from schema properties.

    Returns ``{field_name: {"from": source, "transform": name, ...params}}``.
    """
    if not schema:
        return {}
    props = schema.get("properties", {})
    result: dict[str, dict[str, Any]] = {}
    for name, prop_def in props.items():
        if isinstance(prop_def, dict) and "x-computed" in prop_def:
            result[name] = prop_def["x-computed"]
    return result


# ── Application ──────────────────────────────────────────────────────────


def apply_computed_fields(
    body: dict[str, Any],
    computed_on_write: dict[str, dict[str, Any]],
) -> None:
    """Compute and set all ``x-computed`` fields on *body* (create/replace)."""
    if not computed_on_write:
        return
    for field_name, cfg in computed_on_write.items():
        source_field = cfg.get("from", "")
        source_value = body.get(source_field)
        if source_value is None:
            continue
        transform_name = cfg.get("transform", "")
        fn = _TRANSFORM_REGISTRY.get(transform_name)
        if fn is None:
            continue
        kwargs: dict[str, Any] = {}
        if "max_length" in cfg:
            kwargs["max_length"] = int(cfg["max_length"])
        if "divide_by" in cfg:
            kwargs["divide_by"] = int(cfg["divide_by"])
        body[field_name] = fn(str(source_value), **kwargs)


def apply_computed_fields_partial(
    body: dict[str, Any],
    computed_on_write: dict[str, dict[str, Any]],
) -> None:
    """Recompute only those ``x-computed`` fields whose source is in *body* (patch)."""
    if not computed_on_write:
        return
    for field_name, cfg in computed_on_write.items():
        source_field = cfg.get("from", "")
        if source_field not in body:
            continue
        source_value = body[source_field]
        if source_value is None:
            continue
        transform_name = cfg.get("transform", "")
        fn = _TRANSFORM_REGISTRY.get(transform_name)
        if fn is None:
            continue
        kwargs: dict[str, Any] = {}
        if "max_length" in cfg:
            kwargs["max_length"] = int(cfg["max_length"])
        if "divide_by" in cfg:
            kwargs["divide_by"] = int(cfg["divide_by"])
        body[field_name] = fn(str(source_value), **kwargs)
