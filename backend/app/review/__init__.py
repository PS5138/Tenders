"""Review pipeline: the single writer of question status (``transitions``), the audit trail
(``events``), versions and human-edit re-alignment (``versions``), attest, dispute and gap
acknowledgement (``actions``), the support recompute that owns ``needs_review`` (``support``),
fact invalidation and the expiry sweep (``invalidation``), save-as-answer (``messages``) and
promotion of approved answers into the library (``promotion``).
"""
