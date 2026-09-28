"""The query vector for retrieval.

The plan's rule (draft pipeline inputs): the query vector is the stored ``questions.embedding``
when the query is the question text itself; a rewritten turn or free text is embedded at call
time and never stored. Nothing here writes to the database.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Question
from app.llm import embed


def _as_list(vector: object) -> list[float]:
    """pgvector hands back a numpy array; every caller wants plain floats."""
    return [float(value) for value in vector]  # type: ignore[union-attr]


def get_query_vector(
    session: Session, *, question: Question | None = None, text: str | None = None
) -> list[float]:
    """Return the vector to retrieve with.

    - ``question`` alone: the stored ``questions.embedding``; if that is missing (an extraction
      that has not embedded yet) the question text is embedded now and not stored.
    - ``text`` alone, or ``text`` that differs from the question's text (a rewritten turn or
      free text): embedded at call time, not stored.
    - ``question`` and ``text`` equal to the question's text: the stored embedding, as above.
    """
    if question is None and text is None:
        raise ValueError("get_query_vector needs a question or a text")

    if question is not None and (text is None or text.strip() == question.text.strip()):
        if question.embedding is not None:
            return _as_list(question.embedding)
        return embed([question.text])[0]

    assert text is not None
    if not text.strip():
        raise ValueError("cannot embed an empty query")
    return embed([text])[0]


__all__ = ["get_query_vector"]
