"""LLM and embedding access. Every model call in the code base goes through this package.

- ``client``: ``LLMClient`` protocol, ``AnthropicLLM``, ``FakeLLM`` and the ``get_llm()`` factory.
- ``embeddings``: ``embed(texts)`` with openai / voyage / fake providers.
- ``prompts``: versioned prompt files and ``load_prompt(name, version)``.
"""

from app.llm.client import AnthropicLLM, FakeLLM, LLMClient, LLMError, get_llm, reset_llm
from app.llm.embeddings import embed, get_embedder, reset_embedder
from app.llm.prompts import PROMPT_VERSIONS, load_prompt, prompt_version

__all__ = [
    "PROMPT_VERSIONS",
    "AnthropicLLM",
    "FakeLLM",
    "LLMClient",
    "LLMError",
    "embed",
    "get_embedder",
    "get_llm",
    "load_prompt",
    "prompt_version",
    "reset_embedder",
    "reset_llm",
]
