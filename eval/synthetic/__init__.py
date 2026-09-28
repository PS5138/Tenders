"""Helpers for the synthetic data generator (``eval.generate_synthetic``).

- ``content``: the content brief. Everything sector-specific lives here: the synthetic
  supplier, its dated facts, the question concepts and the sentence pools.
- ``prose``: the deterministic template-based answer writer used offline.
- ``llm_writer``: the Claude-backed answer writer used when a real provider is configured.
- ``documents``: docx writers for the three past-submission layouts and the reference
  documents, and the xlsx writer for the question pack.
- ``ground_truth``: the held-out mapping written to ``ground_truth.json``.
"""
