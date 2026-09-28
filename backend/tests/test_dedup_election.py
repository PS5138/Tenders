"""Ingestion steps 5 and 6: embedding, clustering and canonical election."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.ingest.dedup import deduplicate, reelect_canonical
from app.ingest.embed import embed_items
from app.llm.embeddings import cosine_similarity, embed
from tests.test_supersession_factories import ORG, make_document, make_item, make_section

ANSWER = (
    "We hold ISO 27001 certification covering the whole clinical platform and review the "
    "information security management system annually."
)


def test_embed_items_fills_both_vectors_for_pairs_and_answer_only_for_chunks(
    db_session: Session,
) -> None:
    document = make_document(db_session, doc_type="past_submission")
    section = make_section(db_session, document, ANSWER, heading_path=["4", "4.2 Security"])
    pair = make_item(
        db_session, document, section, question_text="Describe your certification.",
        with_embeddings=False,
    )
    chunk = make_item(db_session, document, section, item_type="chunk", with_embeddings=False)
    assert pair.answer_embedding is None and chunk.answer_embedding is None

    embed_items(db_session, [pair, chunk])

    assert list(pair.question_embedding) == embed(["Describe your certification."])[0]
    assert list(pair.answer_embedding) == embed([ANSWER])[0]
    assert chunk.question_embedding is None
    # The chunk's vector is the heading-path-prefixed text; the stored text is the raw slice.
    assert list(chunk.answer_embedding) == embed(["4 > 4.2 Security\n" + ANSWER])[0]
    assert chunk.answer_text == ANSWER


def test_near_duplicates_cluster_and_newest_effective_date_wins(db_session: Session) -> None:
    older_doc = make_document(
        db_session, doc_type="past_submission", effective_date=date(2024, 3, 1)
    )
    newer_doc = make_document(
        db_session, doc_type="past_submission", effective_date=date(2025, 6, 1)
    )
    older = make_item(db_session, older_doc, answer_text=ANSWER, canonical=False)
    deduplicate(db_session, ORG, [older])
    assert older.is_canonical and older.canonical_id is None

    newer = make_item(db_session, newer_doc, answer_text=ANSWER, canonical=False)
    deduplicate(db_session, ORG, [newer])

    assert newer.is_canonical and newer.canonical_id is None
    assert not older.is_canonical and older.canonical_id == newer.id

    unrelated = make_item(
        db_session,
        newer_doc,
        answer_text="Our social value plan funds two apprenticeships in each contract year.",
        canonical=False,
    )
    deduplicate(db_session, ORG, [unrelated])
    assert unrelated.is_canonical and unrelated.canonical_id is None
    assert cosine_similarity(unrelated.answer_embedding, newer.answer_embedding) < 0.9


def test_promoted_answer_beats_a_pair_regardless_of_date(db_session: Session) -> None:
    pair_doc = make_document(
        db_session, doc_type="past_submission", effective_date=date(2026, 1, 1)
    )
    promoted_doc = make_document(
        db_session, doc_type="past_submission", effective_date=date(2023, 1, 1)
    )
    pair = make_item(db_session, pair_doc, answer_text=ANSWER, canonical=False)
    deduplicate(db_session, ORG, [pair])
    promoted = make_item(
        db_session, promoted_doc, answer_text=ANSWER, item_type="promoted_answer", canonical=False
    )
    deduplicate(db_session, ORG, [promoted])

    assert promoted.is_canonical and promoted.canonical_id is None
    assert pair.canonical_id == promoted.id and not pair.is_canonical


def test_chunks_never_cluster(db_session: Session) -> None:
    document = make_document(db_session)
    first = make_item(db_session, document, item_type="chunk", answer_text=ANSWER, canonical=False)
    second = make_item(db_session, document, item_type="chunk", answer_text=ANSWER, canonical=False)
    deduplicate(db_session, ORG, [first, second])
    assert first.is_canonical and second.is_canonical
    assert first.canonical_id is None and second.canonical_id is None


def test_reelection_after_the_canonical_is_removed(db_session: Session) -> None:
    doc_a = make_document(db_session, doc_type="past_submission", effective_date=date(2024, 1, 1))
    doc_b = make_document(db_session, doc_type="past_submission", effective_date=date(2025, 1, 1))
    doc_c = make_document(db_session, doc_type="past_submission", effective_date=date(2024, 6, 1))
    a = make_item(db_session, doc_a, answer_text=ANSWER, canonical=False)
    b = make_item(db_session, doc_b, answer_text=ANSWER, canonical=False)
    c = make_item(db_session, doc_c, answer_text=ANSWER, canonical=False)
    deduplicate(db_session, ORG, [a, b, c])
    assert b.is_canonical and a.canonical_id == b.id and c.canonical_id == b.id

    db_session.delete(b)
    db_session.flush()
    winner = reelect_canonical(db_session, b.id)

    assert winner is c, "the newest remaining effective date wins"
    assert c.is_canonical and c.canonical_id is None
    assert a.canonical_id == c.id and not a.is_canonical


def test_a_fixed_item_is_detached_and_re_placed(db_session: Session) -> None:
    doc = make_document(db_session, doc_type="past_submission")
    a = make_item(db_session, doc, answer_text=ANSWER, canonical=False)
    b = make_item(db_session, doc, answer_text=ANSWER, canonical=False)
    deduplicate(db_session, ORG, [a, b])
    variant = a if a.canonical_id is not None else b
    canonical = b if variant is a else a
    assert variant.canonical_id == canonical.id

    # The variant is corrected to a completely different answer and re-placed.
    variant.answer_text = "We deliver training through a named onboarding lead per site."
    variant.answer_embedding = embed([variant.answer_text])[0]
    deduplicate(db_session, ORG, [variant])

    assert variant.is_canonical and variant.canonical_id is None
    assert canonical.is_canonical and canonical.canonical_id is None
