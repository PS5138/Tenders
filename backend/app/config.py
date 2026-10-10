"""Application settings, thresholds and fixed lists.

Everything a pipeline module or the evaluation harness tunes lives here. Sector-specific
content is confined to the fixed lists below and the default taxonomy; nothing else in the
code base may hard-code it.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

# Fixed identifiers for seeded rows (see app/db/seed.py).
DEFAULT_ORG_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
FIXTURE_DOCUMENT_ID = uuid.UUID("00000000-0000-4000-8000-0000000000d0")

# Default topic taxonomy, seeded per organisation and editable there. Identifiers referenced by
# FACT_KINDS may not be removed or renamed; the seed asserts this.
DEFAULT_TOPIC_TAXONOMY: tuple[str, ...] = (
    "clinical_safety",
    "information_governance",
    "information_security",
    "interoperability",
    "implementation_and_onboarding",
    "training_and_support",
    "commercial_and_pricing",
    "social_value",
    "company_and_experience",
    "product_functionality",
    "service_levels",
)


@dataclass(frozen=True)
class FactKind:
    """One entry of the fixed fact-kind list.

    ``key_rule`` is handed to the extraction prompt verbatim so keys are comparable across
    documents. ``keyed`` is False for kinds with one current holder (their fact_key is null).
    """

    name: str
    topic: str
    key_rule: str
    keyed: bool


_NULL_KEY = "Always null: only one such fact can be current at once."

FACT_KINDS: dict[str, FactKind] = {
    kind.name: kind
    for kind in (
        FactKind(
            "dspt_status",
            "information_security",
            "The legal entity the assessment belongs to: its ODS code if the text states "
            "one, otherwise null.",
            True,
        ),
        FactKind(
            "cyber_essentials_plus",
            "information_security",
            "Always null; the certificate number belongs in value.",
            False,
        ),
        FactKind(
            "iso_27001",
            "information_security",
            "Always null; the certificate number belongs in value.",
            False,
        ),
        FactKind(
            "clinical_safety_case",
            "clinical_safety",
            "The name of the product the safety case covers.",
            True,
        ),
        FactKind("clinical_safety_officer", "clinical_safety", _NULL_KEY, False),
        FactKind("mhra_registration", "clinical_safety", _NULL_KEY, False),
        FactKind("data_protection_officer", "information_governance", _NULL_KEY, False),
        FactKind(
            "insurance_cover",
            "company_and_experience",
            "The cover type, one of: public liability, professional indemnity, "
            "employer's liability, cyber.",
            True,
        ),
        FactKind("headcount", "company_and_experience", _NULL_KEY, False),
        FactKind("company_registration", "company_and_experience", _NULL_KEY, False),
        FactKind(
            "accreditation_other",
            "company_and_experience",
            "The accreditation name as stated.",
            True,
        ),
    )
}

DOC_TYPES: tuple[str, ...] = ("past_submission", "reference", "tender_document")

# Reference-document kinds.
DOC_KINDS: tuple[str, ...] = (
    "dspt_confirmation",
    "cyber_essentials_plus",
    "iso_27001",
    "clinical_safety_case",
    "information_security_policy",
    "product_description",
    "other",
)

# Kinds that can supersede one another: every kind except product_description and other.
SUPERSEDABLE_DOC_KINDS: tuple[str, ...] = tuple(
    kind for kind in DOC_KINDS if kind not in ("product_description", "other")
)

# Kinds whose facts carry a fact_key, so more than one document can be current at once and
# automatic document supersession never fires.
KEYED_DOC_KINDS: tuple[str, ...] = ("dspt_confirmation", "clinical_safety_case")

TENDER_DOC_KINDS: tuple[str, ...] = (
    "question_pack",
    "specification",
    "clarification_log",
    "contract_terms",
    "other",
)

# Procurement-generic question boilerplate removed before the lexical query is built.
# Tuned against the harness; it must never contain sector terms.
LEXICAL_STOPWORDS: frozenset[str] = frozenset(
    {
        "describe",
        "detail",
        "explain",
        "outline",
        "provide",
        "confirm",
        "please",
        "supplier",
        "bidder",
        "organisation",
        "organization",
        "approach",
        "ensure",
        "requirement",
    }
)

# Delimiters used when a table or spreadsheet row is persisted as one section.
TABLE_ROW_DELIMITER = "\n"
TABLE_CELL_DELIMITER = " | "

LLMProvider = Literal["anthropic", "fake"]
EmbeddingProvider = Literal["openai", "voyage", "fake"]


class Settings(BaseSettings):
    """Environment-driven settings. Field names map to upper-cased environment variables.

    The ``.env`` file is the one at the repository root whatever the process's working
    directory is (the README runs uvicorn, the worker and alembic from ``backend/``); a missing
    file, as in the container, is ignored. Environment variables override it.
    """

    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # Infrastructure
    database_url: str = "postgresql+psycopg://tenders:tenders@localhost:5432/tenders"
    storage_path: Path = REPO_ROOT / "storage"
    # Largest file either upload route accepts, in bytes (default 25 MiB). Both routes read the
    # upload in chunks and answer 413 as soon as the limit is passed, before the synthetic
    # checksum, any storage write or the document row.
    max_upload_bytes: int = 25 * 1024 * 1024
    docling_artifacts_path: Path | None = None
    default_org_id: uuid.UUID = DEFAULT_ORG_ID
    fixture_document_path: Path = REPO_ROOT / "eval" / "data" / "fixtures" / "fixture_document.json"
    log_level: str = "INFO"
    service_secret: str | None = None
    synthetic_demo: bool = False
    # Browser origins allowed to call the API (the Next.js front end), comma-separated and
    # never "*": each environment lists its own origins so the unauthenticated API is not
    # callable from any page. Read by ``app.main.cors_origins``.
    cors_origins: str = "http://localhost:3000"

    # Providers and models
    llm_provider: LLMProvider = "anthropic"
    embedding_provider: EmbeddingProvider = "openai"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    voyage_api_key: str | None = None
    model_main: str = "claude-opus-5"  # synthesis, extraction, query rewrite
    model_fast: str = "claude-sonnet-5"  # classification, coverage judgement, entailment, topics
    embedding_model_openai: str = "text-embedding-3-small"
    embedding_model_voyage: str = "voyage-3"
    embedding_dimension: int = 1536
    # Server-side refusal fallback (``fallbacks: "default"``) on the models that accept it; see
    # ``app.llm.client``. On by default; REFUSAL_FALLBACKS=false turns it off.
    refusal_fallbacks: bool = True

    # Thresholds
    dedup_threshold: float = 0.90
    verbatim_threshold: float = 0.92
    coverage_floor: float = 0.45  # cosine scale; re-base against the harness before the demo
    realign_min: float = 0.85  # string ratio, not embedding cosine
    span_match_min: float = 0.90  # string ratio
    extraction_fidelity_target: float = 0.90
    pair_extraction_window_tokens: int = 6000
    max_attempts: int = 3
    chunk_target_tokens: int = 400
    rrf_k: int = 60
    candidates_per_list: int = 20
    top_k_synthesis: int = 8
    coverage_judgement_top: int = 3
    # Candidates and facts shown to the requirement judgement (``app.retrieve.requirements``).
    # The suggestion reuses ``coverage_floor`` as its cost gate and ``triage_concurrency`` as
    # its pool size.
    requirement_judgement_top: int = 5
    requirement_judgement_facts: int = 8
    triage_concurrency: int = 15
    draft_all_concurrency: int = 4

    # Worker
    worker_poll_interval_seconds: float = 1.0
    expiry_sweep_interval_seconds: int = 3600

    @field_validator("anthropic_api_key", "openai_api_key", "voyage_api_key", mode="before")
    @classmethod
    def _empty_key_is_unset(cls, value: object) -> object:
        """``ANTHROPIC_API_KEY=`` in a ``.env`` (as ``.env.example`` ships it) reads as ``''``.

        Map blank strings to ``None`` so "unset" has one meaning: the SDKs then apply their own
        environment and profile fallbacks, and ``check_provider_configuration`` can report the
        missing key at start-up instead of the first job failing on it.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    # Fixed lists, exposed on the settings object so pipeline code reads one thing. They are
    # module constants and are not environment-overridable.
    @property
    def fact_kinds(self) -> dict[str, FactKind]:
        return FACT_KINDS

    @property
    def doc_types(self) -> tuple[str, ...]:
        return DOC_TYPES

    @property
    def doc_kinds(self) -> tuple[str, ...]:
        return DOC_KINDS

    @property
    def supersedable_doc_kinds(self) -> tuple[str, ...]:
        return SUPERSEDABLE_DOC_KINDS

    @property
    def keyed_doc_kinds(self) -> tuple[str, ...]:
        return KEYED_DOC_KINDS

    @property
    def tender_doc_kinds(self) -> tuple[str, ...]:
        return TENDER_DOC_KINDS

    @property
    def default_topic_taxonomy(self) -> tuple[str, ...]:
        return DEFAULT_TOPIC_TAXONOMY

    @property
    def lexical_stopwords(self) -> frozenset[str]:
        return LEXICAL_STOPWORDS

    @property
    def table_row_delimiter(self) -> str:
        return TABLE_ROW_DELIMITER

    @property
    def table_cell_delimiter(self) -> str:
        return TABLE_CELL_DELIMITER


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings. Tests call ``get_settings.cache_clear()`` after changing the environment."""
    return Settings()


class ProviderConfigurationError(RuntimeError):
    """A real provider is selected but has no credential to run with."""


# Environment variables the SDKs accept in place of the API key field. ``ANTHROPIC_API_KEY``,
# ``OPENAI_API_KEY`` and ``VOYAGE_API_KEY`` themselves are already read into the settings.
_ANTHROPIC_ALTERNATIVE_ENV: tuple[str, ...] = ("ANTHROPIC_AUTH_TOKEN",)


def check_provider_configuration(settings: Settings) -> None:
    """Raise ``ProviderConfigurationError`` when the selected LLM or embedding provider has no key.

    Called at process start by the API and the worker so a blank ``ANTHROPIC_API_KEY`` surfaces
    as a start-up failure with a plain message, not as the first ingest job failing three times.
    Builds no client and touches no network; the ``fake`` providers always pass.
    """
    if settings.synthetic_demo and (
        settings.llm_provider != "fake" or settings.embedding_provider != "fake"
    ):
        raise ProviderConfigurationError("SYNTHETIC_DEMO requires both providers to be fake.")
    if settings.service_secret is not None and len(settings.service_secret) < 32:
        raise ProviderConfigurationError("SERVICE_SECRET must contain at least 32 characters.")
    if settings.llm_provider == "anthropic" and settings.anthropic_api_key is None:
        if not any(os.environ.get(name) for name in _ANTHROPIC_ALTERNATIVE_ENV):
            raise ProviderConfigurationError(
                "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set "
                "(set it in the environment or the repo-root .env, or use LLM_PROVIDER=fake)"
            )
    if settings.embedding_provider == "openai" and settings.openai_api_key is None:
        raise ProviderConfigurationError(
            "EMBEDDING_PROVIDER=openai but OPENAI_API_KEY is not set "
            "(set it in the environment or the repo-root .env, or use EMBEDDING_PROVIDER=fake)"
        )
    if settings.embedding_provider == "voyage" and settings.voyage_api_key is None:
        raise ProviderConfigurationError(
            "EMBEDDING_PROVIDER=voyage but VOYAGE_API_KEY is not set "
            "(set it in the environment or the repo-root .env, or use EMBEDDING_PROVIDER=fake)"
        )
