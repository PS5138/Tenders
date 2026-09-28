"""Versioned prompt files used only by the evaluation harness.

The product's prompts live in ``backend/app/llm/prompts``; these two exist for the harness
alone (the LLM judge of draft quality and the optional LLM rerank toggle) and follow the same
convention: ``<name>.<version>.md`` on disk, one current version per name, and a
``prompt_version(name)`` string such as ``judge.v1`` recorded in every report.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

EVAL_PROMPTS_DIR = Path(__file__).resolve().parent

JUDGE_PROMPT = "judge"
RERANK_PROMPT = "rerank"

EVAL_PROMPT_VERSIONS: dict[str, str] = {
    JUDGE_PROMPT: "v1",
    RERANK_PROMPT: "v1",
}


class EvalPromptNotFound(FileNotFoundError):
    pass


@lru_cache(maxsize=8)
def load_eval_prompt(name: str, version: str | None = None) -> str:
    """Return the text of ``eval/prompts/<name>.<version>.md``."""
    if version is None:
        try:
            version = EVAL_PROMPT_VERSIONS[name]
        except KeyError as exc:
            raise EvalPromptNotFound(f"no harness prompt named {name!r}") from exc
    path = EVAL_PROMPTS_DIR / f"{name}.{version}.md"
    if not path.exists():
        raise EvalPromptNotFound(f"harness prompt file missing: {path}")
    return path.read_text(encoding="utf-8")


def eval_prompt_version(name: str) -> str:
    """The string recorded in reports, e.g. ``"judge.v1"``."""
    return f"{name}.{EVAL_PROMPT_VERSIONS[name]}"


__all__ = [
    "EVAL_PROMPTS_DIR",
    "EVAL_PROMPT_VERSIONS",
    "JUDGE_PROMPT",
    "RERANK_PROMPT",
    "EvalPromptNotFound",
    "eval_prompt_version",
    "load_eval_prompt",
]
