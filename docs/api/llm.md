# LLM Service

The LLM service provides a unified interface to **OpenAI**, **Azure OpenAI**, and **Google Gemini** via their native SDKs (`openai`, `google-genai`), with streaming support, structured output, and automatic provider detection from environment variables or manifest `llm_config`.

## LLMService

::: mdb_engine.llm.service.LLMService
    options:
      show_root_heading: true
      members_order: source
