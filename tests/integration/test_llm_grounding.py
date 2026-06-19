"""Live integration tests for Gemini Google Search grounding via ``LLMService``.

These tests make real network calls to the Gemini API and are therefore
**skipped by default** (including in CI). They only run when BOTH:

- ``GEMINI_API_KEY`` (or ``GOOGLE_API_KEY``) is set to a real key, and
- ``RUN_LIVE_LLM_TESTS=1`` is set (explicit opt-in).

Run locally with::

    RUN_LIVE_LLM_TESTS=1 GEMINI_API_KEY=... \
        pytest tests/integration/test_llm_grounding.py -v -m integration
"""

from __future__ import annotations

import json
import os

import pytest

from mdb_engine.llm import GroundedCompletion, get_llm_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")) or not os.getenv("RUN_LIVE_LLM_TESTS"),
        reason="Live Gemini grounding tests require a real GEMINI_API_KEY and RUN_LIVE_LLM_TESTS=1",
    ),
]

# A prompt that genuinely requires up-to-date web knowledge so grounding kicks in.
_LIVE_PROMPT = "What are some notable AI developments that happened this week? Cite your sources."

_GEMINI_MODEL = os.getenv("LIVE_GEMINI_MODEL", "gemini/gemini-3-flash-preview")


@pytest.fixture
def llm_service():
    """LLMService wired to a single Gemini ``chat`` provider for live tests."""
    return get_llm_service(config={"providers": {"chat": _GEMINI_MODEL}})


@pytest.mark.asyncio
async def test_non_streaming_grounded_call_returns_citations(llm_service):
    """A grounded non-streaming call should return text and >=1 citation."""
    result = await llm_service.chat_completion(
        messages=[{"role": "user", "content": _LIVE_PROMPT}],
        provider_name="chat",
        enable_web_search=True,
        return_metadata=True,
    )

    assert isinstance(result, GroundedCompletion)
    assert result.text
    assert result.grounded is True
    assert len(result.citations) >= 1
    for citation in result.citations:
        assert citation["uri"].startswith("http")
        assert "title" in citation


@pytest.mark.asyncio
async def test_streaming_grounded_call_emits_grounding_event(llm_service):
    """A grounded streaming call should yield content and a trailing event."""
    content_chunks: list[str] = []
    grounding_events: list[str] = []

    async for chunk in llm_service.chat_completion_stream(
        messages=[{"role": "user", "content": _LIVE_PROMPT}],
        provider_name="chat",
        enable_web_search=True,
    ):
        if chunk.startswith("__GROUNDING__:"):
            grounding_events.append(chunk)
        elif not chunk.startswith("__REASONING"):
            content_chunks.append(chunk)

    assert "".join(content_chunks).strip()
    assert len(grounding_events) == 1
    payload = json.loads(grounding_events[0][len("__GROUNDING__:") :])
    assert len(payload["citations"]) >= 1


@pytest.mark.asyncio
async def test_reasoning_effort_with_grounding(llm_service):
    """Thinking + grounding should work together without error."""
    result = await llm_service.chat_completion(
        messages=[{"role": "user", "content": _LIVE_PROMPT}],
        provider_name="chat",
        reasoning_effort="high",
        enable_web_search=True,
        return_metadata=True,
    )

    assert isinstance(result, GroundedCompletion)
    assert result.text
