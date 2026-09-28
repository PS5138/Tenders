"""Versioned prompt files. Prompts live here as ``<name>.<version>.md``, never as inline
strings; every AI answer records the ``prompt_version`` string ``prompt_version(name)``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent

# The current version of each prompt. Bump here when a prompt file changes materially.
PROMPT_VERSIONS: dict[str, str] = {
    "classify_document": "v1",
    "extract_pairs": "v1",
    "chunk_annotate": "v1",
    "extract_questions": "v1",
    "coverage_judgement": "v1",
    "query_rewrite": "v1",
    "synthesis": "v1",
    "entailment": "v1",
    "synthetic_answers": "v1",
}


class PromptNotFound(FileNotFoundError):
    pass


@lru_cache(maxsize=64)
def load_prompt(name: str, version: str | None = None) -> str:
    """Return the text of ``<name>.<version>.md``; ``version`` defaults to PROMPT_VERSIONS."""
    if version is None:
        try:
            version = PROMPT_VERSIONS[name]
        except KeyError as exc:
            raise PromptNotFound(f"no prompt named {name!r} in PROMPT_VERSIONS") from exc
    path = PROMPTS_DIR / f"{name}.{version}.md"
    if not path.exists():
        raise PromptNotFound(f"prompt file missing: {path}")
    return path.read_text(encoding="utf-8")


def prompt_version(name: str) -> str:
    """The string recorded on answers and messages, e.g. ``"synthesis.v1"``."""
    return f"{name}.{PROMPT_VERSIONS[name]}"
