# LLM Service

The LLM service provides a unified interface to **OpenAI**, **Azure OpenAI**, and **Google Gemini** via their native SDKs (`openai`, `google-genai`), with streaming support, structured output, and automatic provider detection from environment variables or manifest `llm_config`.

## Web search / grounding (Gemini)

Pass `enable_web_search=True` to `chat_completion`, `chat_completion_stream`, or `stream` to
enable Google Search grounding for Gemini. For non-streaming calls, add `return_metadata=True`
to receive a `GroundedCompletion` (`text` / `citations` / `grounded` / `model_used` /
`finish_reason`) instead of a plain `str`. Citations are normalized to
`{title, uri, domain, redirect_uri}`.

**Grounding is gated by the model:** `gemini-2.5-*` ground; the `-latest` aliases do **not**. Use
`grounding_policy` so `enable_web_search=True` is never a silent no-op:

- `"best_effort"` (default) — log and continue ungrounded.
- `"require"` — raise `GroundingUnsupportedError`.
- `"auto"` — route the turn to a grounding-capable model (recorded in `model_used`).

## Model capabilities

The engine is the single source of truth for model features. Use
`LLMService.get_capabilities(model=None)`, `list_models(provider=, web_search=, thinking=, vision=)`,
and `supports(feature, model=None)` to build app UIs without hardcoding capability flags. Override
the curated map via manifest `llm_config.model_overrides`.

See the
[LLM Service module README](https://github.com/ranfysvalle02/mdb-engine/blob/main/mdb_engine/llm/README.md)
for full examples (grounding policy, typed streaming events, and the capability matrix).

## LLMService

::: mdb_engine.llm.service.LLMService
    options:
      show_root_heading: true
      members_order: source

## ModelCapabilities

::: mdb_engine.llm.capabilities.ModelCapabilities
    options:
      show_root_heading: true
