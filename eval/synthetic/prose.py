"""Deterministic template-based answer writer.

Used when ``LLM_PROVIDER`` is ``fake`` (and by the tests), so the whole pipeline and the
harness run offline. Every answer is a list of plain paragraphs; there are never headings.

Determinism: the alternatives chosen for a concept depend only on ``seed`` and the concept
id, so two submissions that both answer a concept produce the same base text. ``flips``
then changes that many slots, which is how a "near-identical" variant is made for the
deduplication demo (one flip) and a more distinct held-out answer (two flips).
"""

from __future__ import annotations

import random
from collections.abc import Mapping

from eval.synthetic.content import Concept


def choose_alternatives(concept: Concept, *, seed: int, flips: int = 0) -> list[list[int]]:
    """Per paragraph, per slot: the index of the alternative to use."""
    if concept.paragraphs is None:
        raise ValueError(f"concept {concept.id} has no answer content")
    rng = random.Random(f"{seed}:{concept.id}")
    choices = [[rng.randrange(len(slot)) for slot in paragraph] for paragraph in concept.paragraphs]
    if flips:
        flip_rng = random.Random(f"{seed}:{concept.id}:flip:{flips}")
        positions = [
            (p, s)
            for p, paragraph in enumerate(concept.paragraphs)
            for s, slot in enumerate(paragraph)
            if len(slot) > 1
        ]
        for p, s in flip_rng.sample(positions, min(flips, len(positions))):
            choices[p][s] = (choices[p][s] + 1) % len(concept.paragraphs[p][s])
    return choices


def render_answer(
    concept: Concept, context: Mapping[str, str], *, seed: int, flips: int = 0
) -> list[str]:
    """The answer as a list of paragraphs with every placeholder filled."""
    if concept.paragraphs is None:
        raise ValueError(f"concept {concept.id} has no answer content")
    choices = choose_alternatives(concept, seed=seed, flips=flips)
    paragraphs: list[str] = []
    for paragraph, picks in zip(concept.paragraphs, choices, strict=True):
        sentences = [slot[i].format_map(context) for slot, i in zip(paragraph, picks, strict=True)]
        paragraphs.append(" ".join(sentences))
    return paragraphs


def fixed_sentences(concept: Concept, context: Mapping[str, str]) -> list[str]:
    """Sentences with a single alternative: the dated facts every writer must state verbatim."""
    if concept.paragraphs is None:
        return []
    return [
        slot[0].format_map(context)
        for paragraph in concept.paragraphs
        for slot in paragraph
        if len(slot) == 1
    ]


def word_count(paragraphs: list[str]) -> int:
    return sum(len(paragraph.split()) for paragraph in paragraphs)
