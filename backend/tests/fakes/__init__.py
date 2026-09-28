"""Test doubles that stand in for external providers with rule-based behaviour.

- ``heuristic_llm``: ``HeuristicFakeLLM``, a schema-valid, rule-based ``LLMClient`` for the
  offline end-to-end test of the six MVP steps.
"""

from tests.fakes.heuristic_llm import HeuristicFakeLLM

__all__ = ["HeuristicFakeLLM"]
