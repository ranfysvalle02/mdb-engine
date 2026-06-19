# LLM Service

The LLM service provides a unified interface to **OpenAI**, **Azure OpenAI**, and **Google Gemini** via their native SDKs (`openai`, `google-genai`), with streaming support, structured output, and automatic provider detection from environment variables or manifest `llm_config`.

## Web search / grounding (Gemini)

Pass `enable_web_search=True` to `chat_completion` or `chat_completion_stream` to enable
Google Search grounding for Gemini models. For non-streaming calls, add `return_metadata=True`
to receive a `GroundedCompletion` (`text` / `citations` / `grounded`) instead of a plain `str`.
While streaming, citations arrive as a single trailing `__GROUNDING__:{json}` event (mirroring
the `__REASONING__:` convention). Grounding is ignored with a warning for non-Gemini providers
and is dropped (with a warning) when combined with a JSON `response_format`. See the
[LLM Service module README](https://github.com/ranfysvalle02/mdb-engine/blob/main/mdb_engine/llm/README.md)
for full examples.

## LLMService

::: mdb_engine.llm.service.LLMService
    options:
      show_root_heading: true
      members_order: source
