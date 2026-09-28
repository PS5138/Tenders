"""The content brief for the synthetic corpus.

This is the one place in the generator where sector vocabulary appears. The code that
turns it into documents (``prose``, ``documents``, ``ground_truth``) knows nothing about
health technology; it consumes the structures defined here.

Everything named in this file is invented: the supplier, its people, its certificates, the
buyers and the tender references. None of it describes a real organisation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# ---------------------------------------------------------------------------------------------
# The synthetic supplier
# ---------------------------------------------------------------------------------------------

COMPANY: dict[str, str] = {
    "company": "Northlight Health Informatics Ltd",
    "short": "Northlight",
    "product": "Northlight Connect",
    "product_desc": "a cloud-hosted clinical communication, task management and referral platform",
    "company_number": "11482930",
    "ico_reg": "ZB482913",
    "ods_code": "8JX45",
    "registered_office": "Riverside Business Park, Sheffield",
    "cso": "Dr Helen Okafor",
    "cso_background": "a registered general nurse with fourteen years of acute clinical experience",
    "dpo": "Marcus Whitfield",
    "dpo_email": "dpo@northlight-health.example",
    "cert_body": "Albion Certification Services Ltd",
    "ce_body": "Greyfriars Cyber Assurance Ltd",
    "pl_cover": "£10 million",
    "pi_cover": "£5 million",
    "el_cover": "£10 million",
    "cyber_cover": "£5 million",
    "sla_availability": "99.9 per cent",
    "p1_response": "thirty minutes",
    "p1_resolution": "four hours",
    "founded": "2016",
}


@dataclass(frozen=True)
class FactSnapshot:
    """The dated facts the supplier could truthfully state on a given day.

    The certificates renew, so a submission written in 2024 cites different numbers from one
    written in 2025; that difference is what the fact-supersession rule has to notice.
    """

    as_at: date
    iso_cert: str
    iso_issued: str
    iso_valid_until: str
    ce_cert: str
    ce_issued: str
    ce_expires: str
    dspt_year: str
    dspt_status: str
    dspt_published: str
    csc_version: str
    csc_approved: str
    headcount: str

    def as_dict(self) -> dict[str, str]:
        return {
            "iso_cert": self.iso_cert,
            "iso_issued": self.iso_issued,
            "iso_valid_until": self.iso_valid_until,
            "ce_cert": self.ce_cert,
            "ce_issued": self.ce_issued,
            "ce_expires": self.ce_expires,
            "dspt_year": self.dspt_year,
            "dspt_status": self.dspt_status,
            "dspt_published": self.dspt_published,
            "csc_version": self.csc_version,
            "csc_approved": self.csc_approved,
            "headcount": self.headcount,
        }


# Ordered oldest to newest; ``facts_as_at`` picks the newest snapshot on or before a date.
FACT_SNAPSHOTS: tuple[FactSnapshot, ...] = (
    FactSnapshot(
        as_at=date(2024, 6, 1),
        iso_cert="NHI-27001-0417",
        iso_issued="12 March 2024",
        iso_valid_until="11 March 2027",
        ce_cert="CEP-0931-2024",
        ce_issued="3 May 2024",
        ce_expires="2 May 2025",
        dspt_year="2023/24",
        dspt_status="Standards Met",
        dspt_published="24 June 2024",
        csc_version="2.6",
        csc_approved="19 November 2023",
        headcount="86",
    ),
    FactSnapshot(
        as_at=date(2025, 2, 1),
        iso_cert="NHI-27001-0417",
        iso_issued="12 March 2024",
        iso_valid_until="11 March 2027",
        ce_cert="CEP-0931-2024",
        ce_issued="3 May 2024",
        ce_expires="2 May 2025",
        dspt_year="2023/24",
        dspt_status="Standards Met",
        dspt_published="24 June 2024",
        csc_version="3.2",
        csc_approved="14 January 2025",
        headcount="91",
    ),
    FactSnapshot(
        as_at=date(2025, 7, 1),
        iso_cert="NHI-27001-0582",
        iso_issued="9 March 2025",
        iso_valid_until="11 March 2027",
        ce_cert="CEP-1147-2025",
        ce_issued="29 April 2025",
        ce_expires="28 April 2026",
        dspt_year="2024/25",
        dspt_status="Standards Met",
        dspt_published="27 June 2025",
        csc_version="3.2",
        csc_approved="14 January 2025",
        headcount="94",
    ),
)


def facts_as_at(when: date) -> FactSnapshot:
    chosen = FACT_SNAPSHOTS[0]
    for snapshot in FACT_SNAPSHOTS:
        if snapshot.as_at <= when:
            chosen = snapshot
    return chosen


def format_date(value: date) -> str:
    """British long date without a leading zero, e.g. ``14 June 2024``."""
    return f"{value.day} {value.strftime('%B %Y')}"


# ---------------------------------------------------------------------------------------------
# Sections of an NHS-style question pack
# ---------------------------------------------------------------------------------------------

SECTIONS: dict[str, str] = {
    "cs": "1. Clinical safety",
    "ig": "2. Information governance",
    "is": "3. Information security",
    "io": "4. Interoperability",
    "im": "5. Implementation and onboarding",
    "tr": "6. Training and support",
    "co": "7. Commercial",
    "sv": "8. Social value",
    "cx": "9. Company and experience",
}

SECTION_TITLES: dict[str, str] = {
    key: label.split(". ", 1)[1] for key, label in SECTIONS.items()
}


# ---------------------------------------------------------------------------------------------
# Question concepts
# ---------------------------------------------------------------------------------------------

# A slot is a tuple of alternative sentences; the prose engine picks one per slot. A slot with
# one alternative is fixed text, which is how dated facts are pinned so the fact extractor
# finds a single copyable sentence. Paragraphs are lists of slots.
Slot = tuple[str, ...]
Paragraph = list[Slot]


@dataclass(frozen=True)
class Concept:
    id: str
    section: str  # key into SECTIONS
    topic: str  # from the default taxonomy
    question: str
    variants: tuple[str, ...] = ()
    response_type: str = "free_text"  # free_text | yes_no | attachment | pricing
    word_limit: int | None = 500
    weighting: int | None = 5
    mandatory: bool = False
    paragraphs: list[Paragraph] | None = None
    brief: str = ""  # one-line content brief for the Claude writer

    @property
    def answerable(self) -> bool:
        return self.paragraphs is not None

    def question_variant(self, index: int) -> str:
        options = (self.question, *self.variants)
        return options[index % len(options)]


def _c(**kwargs: object) -> Concept:
    return Concept(**kwargs)  # type: ignore[arg-type]


CONCEPTS: dict[str, Concept] = {}


def _register(concept: Concept) -> None:
    if concept.id in CONCEPTS:
        raise ValueError(f"duplicate concept {concept.id}")
    CONCEPTS[concept.id] = concept


# --- Clinical safety -----------------------------------------------------------------------

_register(
    _c(
        id="cs_dcb0129",
        section="cs",
        topic="clinical_safety",
        question=(
            "Describe how your organisation complies with DCB0129 and how the clinical safety "
            "case for the proposed solution is maintained."
        ),
        variants=(
            "Please describe your compliance with the DCB0129 clinical risk management "
            "standard, including the clinical safety case for the product offered.",
            "Explain how clinical risk is managed for the solution in line with DCB0129 and "
            "how the clinical safety case report is kept current.",
        ),
        word_limit=600,
        weighting=8,
        mandatory=True,
        brief=(
            "DCB0129 compliance: clinical risk management system, clinical safety case for the "
            "product, hazard log, safety case report version and approval date, review cadence."
        ),
        paragraphs=[
            [
                (
                    "{company} operates a clinical risk management system that meets the "
                    "requirements of DCB0129, the NHS standard for manufacturers of health IT.",
                    "{short} manages clinical risk for {product} under a clinical risk "
                    "management system built to DCB0129, the standard that applies to "
                    "manufacturers of health IT systems.",
                ),
                (
                    "The system is owned by our Clinical Safety Officer and is reviewed by the "
                    "board twice a year.",
                    "Ownership sits with our Clinical Safety Officer, and the board reviews the "
                    "system every six months.",
                ),
                (
                    "Every release of {product} passes through hazard identification, risk "
                    "assessment and risk control before it is approved for deployment.",
                    "No release of {product} is approved for deployment until hazard "
                    "identification, risk assessment and risk control have been completed for it.",
                ),
            ],
            [
                (
                    "The clinical safety case report for {product} is at version "
                    "{csc_version} and was approved by our Clinical Safety Officer on "
                    "{csc_approved}.",
                ),
                (
                    "The report draws on a hazard log of forty-two identified hazards, each with "
                    "its initial and residual risk rating and the controls that reduce it.",
                    "It is supported by a hazard log that records forty-two hazards with their "
                    "initial and residual risk ratings and the controls applied to each.",
                ),
                (
                    "We review the safety case at every major release and at least annually, "
                    "and we will share the current report with {buyer} under a confidentiality "
                    "agreement.",
                    "The safety case is reviewed at each major release and no less than once a "
                    "year, and the current report is available to {buyer} under a "
                    "confidentiality agreement.",
                ),
            ],
            [
                (
                    "Clinical safety incidents reported by customers are triaged within one "
                    "working day, entered in the hazard log where they reveal a new or changed "
                    "hazard, and closed only when the Clinical Safety Officer has signed off "
                    "the resolution.",
                    "When a customer reports a clinical safety incident it is triaged within one "
                    "working day, recorded against the hazard log where a hazard is new or "
                    "changed, and closed only with the Clinical Safety Officer's sign-off.",
                ),
            ],
        ],
    )
)

_register(
    _c(
        id="cs_officer",
        section="cs",
        topic="clinical_safety",
        question=(
            "Provide the name and qualifications of your Clinical Safety Officer and describe "
            "their role in the delivery of this contract."
        ),
        variants=(
            "Who is your named Clinical Safety Officer, what are their qualifications, and how "
            "will they be involved in this contract?",
        ),
        word_limit=300,
        weighting=4,
        mandatory=True,
        brief=(
            "Named Clinical Safety Officer, registration and training, their duties during "
            "deployment and in service."
        ),
        paragraphs=[
            [
                (
                    "Our Clinical Safety Officer is {cso}, {cso_background}.",
                ),
                (
                    "{cso} holds current professional registration and has completed accredited "
                    "training in DCB0129 and DCB0160 clinical risk management.",
                    "{cso} maintains professional registration and is trained to the accredited "
                    "DCB0129 and DCB0160 clinical risk management syllabus.",
                ),
                (
                    "She reports to the Chief Medical Officer and has authority to halt a release "
                    "on clinical safety grounds.",
                    "She reports directly to the Chief Medical Officer and can stop any release "
                    "on clinical safety grounds.",
                ),
            ],
            [
                (
                    "During implementation {cso} will attend the joint clinical safety workshops "
                    "with the {buyer} Clinical Safety Officer, contribute to the deployment "
                    "hazard log and sign the clinical safety closure statement before go-live.",
                    "For this contract {cso} will join the {buyer} Clinical Safety Officer in "
                    "the deployment hazard workshops, maintain our side of the deployment hazard "
                    "log and sign the clinical safety closure statement ahead of go-live.",
                ),
                (
                    "In live service she reviews every clinical safety incident and chairs the "
                    "quarterly clinical safety review with customers.",
                    "Once the service is live she reviews each clinical safety incident and "
                    "chairs a quarterly clinical safety review open to all customers.",
                ),
            ],
        ],
    )
)

_register(
    _c(
        id="cs_hazard_log",
        section="cs",
        topic="clinical_safety",
        question=(
            "Describe how hazards are identified, recorded and controlled throughout the life "
            "of the solution, and how clinical safety incidents are managed."
        ),
        variants=(
            "Explain your hazard log process and how clinical safety incidents are reported, "
            "investigated and resolved.",
        ),
        word_limit=500,
        weighting=5,
        brief=(
            "Hazard identification workshops, hazard log fields and risk matrix, change control "
            "gate, incident triage and root cause, communication to customers."
        ),
        paragraphs=[
            [
                (
                    "Hazards are identified in structured workshops that bring together "
                    "clinicians, product managers and engineers at the start of each release "
                    "cycle.",
                    "At the start of every release cycle clinicians, product managers and "
                    "engineers hold a structured hazard identification workshop.",
                ),
                (
                    "Each hazard is recorded in the hazard log with its causes, effects, "
                    "existing controls, initial risk rating on the DCB0129 five-by-five matrix, "
                    "proposed controls and residual rating.",
                    "The hazard log records, for every hazard, its causes and effects, the "
                    "controls already in place, an initial rating on the DCB0129 five-by-five "
                    "matrix, the further controls proposed and the residual rating.",
                ),
                (
                    "A hazard with a residual rating above three cannot be released without a "
                    "written justification from the Clinical Safety Officer.",
                    "No hazard whose residual rating exceeds three is released without a written "
                    "justification signed by the Clinical Safety Officer.",
                ),
            ],
            [
                (
                    "Clinical safety incidents are logged in our service desk with a dedicated "
                    "category, escalated to the Clinical Safety Officer within one working day "
                    "and investigated using root cause analysis.",
                    "Our service desk carries a dedicated category for clinical safety "
                    "incidents; each is escalated to the Clinical Safety Officer within one "
                    "working day and investigated by root cause analysis.",
                ),
                (
                    "Where an incident affects more than one customer we issue a safety notice "
                    "to all affected organisations within two working days.",
                    "If more than one customer is affected we send a safety notice to every "
                    "affected organisation within two working days.",
                ),
                (
                    "The hazard log and incident register are reviewed together at the quarterly "
                    "clinical safety review.",
                    "Both the hazard log and the incident register are examined at each "
                    "quarterly clinical safety review.",
                ),
            ],
        ],
    )
)

_register(
    _c(
        id="cs_dcb0160_support",
        section="cs",
        topic="clinical_safety",
        question=(
            "Describe how you will support the Trust to discharge its own obligations under "
            "DCB0160 during deployment and in live service."
        ),
        word_limit=400,
        weighting=4,
        brief=(
            "Supplier support for the deploying organisation's DCB0160 duties: sharing the "
            "manufacturer safety case, joint hazard workshops, deployment safety case inputs, "
            "release notes with clinical safety impact statements."
        ),
        paragraphs=[
            [
                (
                    "We recognise that {buyer} carries its own duties under DCB0160 and we "
                    "structure our clinical safety work so that the Trust can discharge them "
                    "with the least effort.",
                    "{buyer} has its own obligations under DCB0160, and we organise our clinical "
                    "safety activities so that the Trust can meet them efficiently.",
                ),
                (
                    "Before deployment we share the manufacturer clinical safety case report and "
                    "the hazard log for {product} with the Trust's Clinical Safety Officer so "
                    "that the deployment safety case can build on them.",
                    "Ahead of deployment the Trust's Clinical Safety Officer receives our "
                    "manufacturer safety case report and the {product} hazard log as inputs to "
                    "the deployment safety case.",
                ),
            ],
            [
                (
                    "We run joint hazard workshops covering local configuration, integration and "
                    "workflow, and we provide a written deployment hazard assessment for each.",
                    "Joint hazard workshops address local configuration, integration and "
                    "workflow, and each produces a written deployment hazard assessment.",
                ),
                (
                    "In live service every release note carries a clinical safety impact "
                    "statement signed by our Clinical Safety Officer, giving the Trust the "
                    "information it needs to update its own hazard log.",
                    "Once live, each release note includes a clinical safety impact statement "
                    "signed by our Clinical Safety Officer so the Trust can update its hazard "
                    "log.",
                ),
            ],
        ],
    )
)

# --- Information governance ----------------------------------------------------------------

_register(
    _c(
        id="ig_dspt",
        section="ig",
        topic="information_governance",
        question=(
            "Confirm your organisation's current Data Security and Protection Toolkit status "
            "and describe how compliance is maintained."
        ),
        variants=(
            "Please state your latest Data Security and Protection Toolkit assessment outcome "
            "and explain how you keep it current.",
            "What is your Data Security and Protection Toolkit status, and how do you maintain "
            "it year on year?",
        ),
        word_limit=300,
        weighting=5,
        mandatory=True,
        brief=(
            "DSPT year, outcome and publication date, ODS code, annual cycle, evidence owner, "
            "staff training completion."
        ),
        paragraphs=[
            [
                (
                    "{company} completed the {dspt_year} Data Security and Protection Toolkit "
                    "assessment with an outcome of {dspt_status}, published on {dspt_published} "
                    "under ODS code {ods_code}.",
                ),
                (
                    "We have achieved Standards Met in every assessment year since we first "
                    "registered.",
                    "Every assessment we have submitted since registering has achieved "
                    "Standards Met.",
                ),
            ],
            [
                (
                    "Compliance is maintained through an annual cycle owned by our Data "
                    "Protection Officer: evidence is refreshed each quarter, the ten data "
                    "security standards are mapped to our ISO 27001 controls, and the board "
                    "approves the submission before publication.",
                    "Our Data Protection Officer runs an annual cycle to keep the assessment "
                    "current: evidence is refreshed quarterly, the ten data security standards "
                    "are mapped to our ISO 27001 controls, and the board signs off the "
                    "submission before it is published.",
                ),
                (
                    "All staff complete data security awareness training within their first "
                    "month and annually thereafter, and completion is above ninety-five per cent.",
                    "Data security awareness training is completed by all staff in their first "
                    "month and every year after that, with completion above ninety-five per cent.",
                ),
            ],
        ],
    )
)

_register(
    _c(
        id="ig_dpo",
        section="ig",
        topic="information_governance",
        question=(
            "Provide details of your Data Protection Officer and describe your information "
            "governance arrangements."
        ),
        variants=(
            "Name your Data Protection Officer and describe the governance structure that "
            "oversees data protection in your organisation.",
        ),
        word_limit=400,
        weighting=4,
        brief=(
            "Named DPO and contact route, ICO registration, IG steering group, policy set, "
            "subject access and breach handling."
        ),
        paragraphs=[
            [
                (
                    "Our Data Protection Officer is {dpo}, who can be contacted at {dpo_email}.",
                ),
                (
                    "{company} is registered with the Information Commissioner's Office under "
                    "registration number {ico_reg}.",
                ),
                (
                    "{dpo} reports to the board, is independent of product delivery and has "
                    "been in post since {founded}.",
                    "{dpo} is independent of the delivery teams, reports to the board and has "
                    "held the role since {founded}.",
                ),
            ],
            [
                (
                    "Information governance is overseen by a steering group chaired by the Data "
                    "Protection Officer that meets monthly and reviews incidents, subject access "
                    "requests, policy changes and audit findings.",
                    "A monthly information governance steering group, chaired by the Data "
                    "Protection Officer, reviews incidents, subject access requests, policy "
                    "changes and audit findings.",
                ),
                (
                    "Our policy set covers data protection, records management, acceptable use, "
                    "confidentiality and breach management, and each policy is reviewed annually.",
                    "We maintain policies for data protection, records management, acceptable "
                    "use, confidentiality and breach management, each reviewed every year.",
                ),
                (
                    "Personal data breaches are assessed within twenty-four hours and reported to "
                    "the controller and, where required, to the Information Commissioner's "
                    "Office within seventy-two hours.",
                    "A personal data breach is assessed within twenty-four hours and notified to "
                    "the controller, and to the Information Commissioner's Office where "
                    "required, inside seventy-two hours.",
                ),
            ],
        ],
    )
)

_register(
    _c(
        id="ig_dpia",
        section="ig",
        topic="information_governance",
        question=(
            "Describe how you will support the Trust to complete a Data Protection Impact "
            "Assessment and set out the data processing roles you propose."
        ),
        variants=(
            "How will you help us complete our Data Protection Impact Assessment, and what "
            "controller and processor roles apply to the proposed service?",
        ),
        word_limit=400,
        weighting=4,
        brief=(
            "Processor role, DPIA template and supplier annex, data flows, lawful basis "
            "support, data processing agreement."
        ),
        paragraphs=[
            [
                (
                    "For this service {buyer} is the data controller and {company} acts as a "
                    "data processor under a data processing agreement aligned to the NHS "
                    "Standard Contract.",
                    "{company} acts as data processor and {buyer} remains data controller, "
                    "governed by a data processing agreement aligned to the NHS Standard "
                    "Contract.",
                ),
                (
                    "We do not use customer data for any purpose other than delivering the "
                    "contracted service.",
                    "Customer data is used only to deliver the contracted service and for no "
                    "other purpose.",
                ),
            ],
            [
                (
                    "To support the Data Protection Impact Assessment we supply a completed "
                    "supplier annex covering data flows, hosting, sub-processors, retention, "
                    "security controls and international transfers, which in our case there are "
                    "none.",
                    "Our supplier annex to the Data Protection Impact Assessment sets out data "
                    "flows, hosting, sub-processors, retention and security controls, and "
                    "confirms that no international transfers take place.",
                ),
                (
                    "Our Data Protection Officer attends the Trust's assessment workshop and "
                    "responds to follow-up questions within five working days.",
                    "The Trust's assessment workshop is attended by our Data Protection Officer, "
                    "who answers any follow-up questions within five working days.",
                ),
            ],
        ],
    )
)

_register(
    _c(
        id="ig_data_residency",
        section="ig",
        topic="information_governance",
        question=(
            "Where will data be hosted and processed, and confirm that no personal data will "
            "leave the United Kingdom."
        ),
        variants=(
            "State the location of hosting and processing for all personal data and confirm "
            "whether any transfer outside the United Kingdom occurs.",
        ),
        word_limit=250,
        weighting=3,
        mandatory=True,
        brief=(
            "UK-only hosting in two public cloud regions, no offshore support access, "
            "sub-processor list, encryption at rest and in transit."
        ),
        paragraphs=[
            [
                (
                    "All personal data processed by {product} is hosted in two United Kingdom "
                    "public cloud regions, with the second region used for replication and "
                    "disaster recovery.",
                    "{product} hosts all personal data in two public cloud regions located in "
                    "the United Kingdom, the second serving replication and disaster recovery.",
                ),
                (
                    "No personal data is transferred outside the United Kingdom, and our support "
                    "staff access production systems only from within the United Kingdom.",
                    "Personal data never leaves the United Kingdom and production access by our "
                    "support staff is restricted to United Kingdom locations.",
                ),
            ],
            [
                (
                    "Our sub-processors are listed in the data processing agreement and are "
                    "limited to the hosting provider and a United Kingdom messaging gateway.",
                    "The data processing agreement lists our sub-processors, which are the "
                    "hosting provider and a messaging gateway based in the United Kingdom.",
                ),
                (
                    "Data is encrypted in transit with TLS 1.2 or higher and at rest with "
                    "AES-256.",
                    "Encryption is applied in transit using TLS 1.2 or above and at rest using "
                    "AES-256.",
                ),
            ],
        ],
    )
)

_register(
    _c(
        id="ig_retention",
        section="ig",
        topic="information_governance",
        question=(
            "Describe your data retention and deletion arrangements, including what happens to "
            "Trust data at the end of the contract."
        ),
        word_limit=300,
        weighting=3,
        brief=(
            "Retention set by controller, Records Management Code of Practice, deletion "
            "certificate at exit, export formats, timelines."
        ),
        paragraphs=[
            [
                (
                    "Retention periods are set by {buyer} as controller and configured per "
                    "record type in {product}, with defaults drawn from the Records Management "
                    "Code of Practice for Health and Social Care.",
                    "{buyer}, as controller, sets retention periods, which are configured for "
                    "each record type in {product} using defaults taken from the Records "
                    "Management Code of Practice for Health and Social Care.",
                ),
                (
                    "Records that reach the end of their retention period are flagged for review "
                    "and deleted only after the Trust's records manager confirms.",
                    "When a record reaches the end of its retention period it is flagged for "
                    "review and removed only once the Trust's records manager has confirmed.",
                ),
            ],
            [
                (
                    "At the end of the contract we provide a full export of the Trust's data in "
                    "open formats within thirty days of the termination date, and we delete all "
                    "copies, including backups, within ninety days, issuing a signed "
                    "certificate of deletion.",
                    "On contract expiry the Trust receives a complete export of its data in "
                    "open formats within thirty days, and every copy including backups is "
                    "deleted within ninety days, confirmed by a signed certificate of deletion.",
                ),
            ],
        ],
    )
)

# --- Information security ------------------------------------------------------------------

_register(
    _c(
        id="is_iso27001",
        section="is",
        topic="information_security",
        question=(
            "Confirm whether your organisation holds ISO/IEC 27001 certification and provide "
            "the certificate number, scope and date of issue."
        ),
        variants=(
            "Do you hold ISO 27001 certification? Provide the certificate number, the "
            "certifying body, the scope and the date of issue.",
            "Please provide details of your ISO/IEC 27001 certification, including number, "
            "scope and issue date.",
        ),
        word_limit=300,
        weighting=5,
        mandatory=True,
        brief=(
            "ISO/IEC 27001:2022 certificate number, certification body, issue date, validity, "
            "scope covering the product and hosting, surveillance audits."
        ),
        paragraphs=[
            [
                (
                    "{company} holds ISO/IEC 27001:2022 certification for its information "
                    "security management system.",
                    "The information security management system of {company} is certified to "
                    "ISO/IEC 27001:2022.",
                ),
                (
                    "Our ISO/IEC 27001 certificate, number {iso_cert}, was issued on "
                    "{iso_issued} by {cert_body} and is valid until {iso_valid_until}.",
                ),
                (
                    "The scope covers the design, development, hosting and support of "
                    "{product} and all corporate systems that process customer data.",
                    "Certification scope includes the design, development, hosting and support "
                    "of {product} together with every corporate system that handles customer "
                    "data.",
                ),
            ],
            [
                (
                    "Surveillance audits take place annually and the most recent audit raised no "
                    "major non-conformities.",
                    "The certification body audits us every year, and no major "
                    "non-conformities were raised at the latest audit.",
                ),
                (
                    "A copy of the certificate is available on request.",
                    "We can supply a copy of the certificate on request.",
                ),
            ],
        ],
    )
)

_register(
    _c(
        id="is_ce_plus",
        section="is",
        topic="information_security",
        question=(
            "Confirm whether you hold Cyber Essentials Plus certification and provide the "
            "certificate number and expiry date."
        ),
        variants=(
            "Do you hold current Cyber Essentials Plus certification? State the certificate "
            "number, issue date and expiry date.",
        ),
        word_limit=200,
        weighting=3,
        mandatory=True,
        brief=(
            "Cyber Essentials Plus certificate number, certification body, issue and expiry "
            "dates, annual renewal, scope of the whole organisation."
        ),
        paragraphs=[
            [
                (
                    "{company} holds Cyber Essentials Plus certification covering the whole "
                    "organisation.",
                    "The whole of {company} is covered by Cyber Essentials Plus certification.",
                ),
                (
                    "Our Cyber Essentials Plus certificate, number {ce_cert}, was issued on "
                    "{ce_issued} by {ce_body} and expires on {ce_expires}.",
                ),
            ],
            [
                (
                    "The Plus assessment includes an independent technical audit of a sample of "
                    "our devices and of every internet-facing system, in addition to the "
                    "self-assessment questionnaire.",
                    "Beyond the self-assessment questionnaire, the Plus assessment adds an "
                    "independent technical audit of sampled devices and of all internet-facing "
                    "systems.",
                ),
                (
                    "We renew the certification annually and the renewal assessment is scheduled "
                    "six weeks before expiry so that there is no gap in coverage.",
                    "Certification is renewed every year, with the renewal assessment booked six "
                    "weeks ahead of expiry to avoid any lapse.",
                ),
            ],
        ],
    )
)

_register(
    _c(
        id="is_access_control",
        section="is",
        topic="information_security",
        question=(
            "Describe how user access to the solution is controlled, including authentication, "
            "role-based access and audit of access to patient records."
        ),
        variants=(
            "Explain your approach to identity, authentication and role-based access control, "
            "and how access to clinical records is audited.",
        ),
        word_limit=500,
        weighting=5,
        brief=(
            "Single sign-on options, multi-factor authentication, role-based access "
            "configured by the Trust, joiners-movers-leavers, immutable audit log, legitimate "
            "relationship alerts."
        ),
        paragraphs=[
            [
                (
                    "{product} authenticates users through the Trust's identity provider using "
                    "SAML 2.0 or OpenID Connect, so that Trust joiners, movers and leavers "
                    "processes apply automatically.",
                    "Users sign in to {product} through the Trust's own identity provider over "
                    "SAML 2.0 or OpenID Connect, which means the Trust's joiners, movers and "
                    "leavers processes take effect without any action on our side.",
                ),
                (
                    "Where a local account is unavoidable, multi-factor authentication is "
                    "enforced and passwords follow the National Cyber Security Centre guidance.",
                    "Local accounts, where they cannot be avoided, require multi-factor "
                    "authentication and follow National Cyber Security Centre password guidance.",
                ),
            ],
            [
                (
                    "Access is role-based: the Trust's administrators define roles that map to "
                    "job functions, and each role grants the minimum permissions needed.",
                    "Role-based access lets the Trust's administrators define roles that "
                    "correspond to job functions, each granting only the permissions required.",
                ),
                (
                    "Every view of, and change to, a patient record is written to an immutable "
                    "audit log that records the user, time, record and action, and the log can be "
                    "exported to the Trust's audit tooling.",
                    "An immutable audit log captures the user, time, record and action for every "
                    "view of and change to a patient record, and it can be exported to the "
                    "Trust's audit tooling.",
                ),
                (
                    "Alerts are raised when a user accesses records outside their usual team or "
                    "location so that the Trust's privacy officer can review them.",
                    "The Trust's privacy officer receives alerts whenever a user accesses records "
                    "outside their normal team or location.",
                ),
            ],
        ],
    )
)

_register(
    _c(
        id="is_pen_testing",
        section="is",
        topic="information_security",
        question=(
            "Describe your penetration testing and vulnerability management regime, including "
            "frequency, independence of testers and remediation timescales."
        ),
        variants=(
            "How often is the solution penetration tested, by whom, and how quickly are "
            "vulnerabilities remediated?",
        ),
        word_limit=400,
        weighting=4,
        brief=(
            "Annual independent CHECK or CREST penetration test, testing after major "
            "releases, continuous scanning, remediation SLAs by severity, summary shared with "
            "customers."
        ),
        paragraphs=[
            [
                (
                    "{product} is penetration tested at least annually by an independent "
                    "CREST-accredited firm and again after any major architectural change.",
                    "An independent CREST-accredited firm penetration tests {product} every "
                    "year and after every major architectural change.",
                ),
                (
                    "The test covers the web application, the public interfaces and the hosting "
                    "environment, and a summary report is made available to customers.",
                    "Testing covers the web application, its public interfaces and the hosting "
                    "environment, and customers receive a summary report.",
                ),
            ],
            [
                (
                    "Between tests we run automated vulnerability scanning of dependencies and "
                    "infrastructure on every build and weekly against production.",
                    "Automated vulnerability scanning runs against dependencies and "
                    "infrastructure on every build, and weekly against production.",
                ),
                (
                    "Critical vulnerabilities are remediated within seven days, high within "
                    "thirty days and medium within ninety days, and the security lead reports "
                    "progress to the board monthly.",
                    "We fix critical vulnerabilities within seven days, high within thirty and "
                    "medium within ninety, with monthly progress reporting to the board by the "
                    "security lead.",
                ),
            ],
        ],
    )
)

# --- Interoperability ----------------------------------------------------------------------

_register(
    _c(
        id="io_standards",
        section="io",
        topic="interoperability",
        question=(
            "Describe the interoperability standards the solution supports and its integration "
            "with NHS national services."
        ),
        variants=(
            "Which open standards does the solution support, and how does it connect to "
            "national NHS services such as the Personal Demographics Service?",
            "Set out the interoperability standards supported and your integration with "
            "national NHS infrastructure.",
        ),
        word_limit=500,
        weighting=6,
        mandatory=True,
        brief=(
            "HL7 FHIR R4 UK Core, HL7 v2 for legacy, open APIs, NHS number verification via "
            "PDS, MESH messaging, NHS login for patient access, Spine connectivity."
        ),
        paragraphs=[
            [
                (
                    "{product} exposes and consumes HL7 FHIR R4 resources profiled to UK Core, "
                    "and supports HL7 version 2 messaging for integration with older patient "
                    "administration systems.",
                    "{product} consumes and publishes HL7 FHIR R4 resources conforming to UK "
                    "Core, and it supports HL7 version 2 messaging where an older patient "
                    "administration system requires it.",
                ),
                (
                    "All of our application programming interfaces are documented with OpenAPI "
                    "specifications and are available to the Trust and its other suppliers "
                    "without additional licence fees.",
                    "Every application programming interface is documented in OpenAPI and is "
                    "open to the Trust and its other suppliers at no extra licence cost.",
                ),
            ],
            [
                (
                    "We integrate with the Personal Demographics Service to verify NHS numbers "
                    "and demographics, with MESH for secure messaging to primary care, and with "
                    "NHS login where patients access the service directly.",
                    "National integrations include the Personal Demographics Service for NHS "
                    "number and demographic verification, MESH for secure messaging to primary "
                    "care, and NHS login for direct patient access.",
                ),
                (
                    "Our Spine connection is assured and in live use with existing customers.",
                    "The Spine connection is assured and already live with our current "
                    "customers.",
                ),
            ],
        ],
    )
)

_register(
    _c(
        id="io_integration_epr",
        section="io",
        topic="interoperability",
        question=(
            "Describe how the solution will integrate with the Trust's electronic patient "
            "record and patient administration system, including your approach to integration "
            "testing."
        ),
        variants=(
            "Explain your approach to integrating with our electronic patient record and "
            "patient administration system, and how integration will be tested before go-live.",
        ),
        word_limit=500,
        weighting=6,
        brief=(
            "Integration engine, inbound ADT and outbound results, FHIR APIs to the EPR, "
            "context launch, integration test environment, message volumes and reconciliation."
        ),
        paragraphs=[
            [
                (
                    "Integration with the Trust's electronic patient record and patient "
                    "administration system is delivered through our integration layer, which "
                    "receives admission, discharge and transfer messages and publishes tasks, "
                    "notes and referral outcomes back to the record.",
                    "Our integration layer connects {product} to the Trust's electronic patient "
                    "record and patient administration system, receiving admission, discharge "
                    "and transfer messages and returning tasks, notes and referral outcomes to "
                    "the record.",
                ),
                (
                    "Clinicians launch {product} in patient context from the electronic patient "
                    "record so that no patient is selected twice.",
                    "{product} launches in patient context from the electronic patient record, "
                    "which removes the need to select the patient a second time.",
                ),
            ],
            [
                (
                    "Integration testing takes place in a dedicated test environment connected "
                    "to the Trust's test instances, using an agreed message catalogue and test "
                    "patients.",
                    "We test integration in a dedicated environment linked to the Trust's test "
                    "instances, working through an agreed message catalogue with test patients.",
                ),
                (
                    "We reconcile message counts daily during the first month of live service and "
                    "report any discrepancy to the Trust's integration team the same day.",
                    "For the first month after go-live message counts are reconciled daily, and "
                    "any discrepancy is reported to the Trust's integration team that day.",
                ),
            ],
        ],
    )
)

_register(
    _c(
        id="io_snomed",
        section="io",
        topic="interoperability",
        question=(
            "Describe your use of clinical terminologies, including SNOMED CT and the NHS "
            "number, within the solution."
        ),
        variants=(
            "How does the solution use SNOMED CT, dm+d and the NHS number to record and "
            "exchange clinical information?",
        ),
        word_limit=300,
        weighting=3,
        brief=(
            "SNOMED CT UK edition for coded entries, dm+d for medicines references, NHS number "
            "as primary identifier, terminology server updates."
        ),
        paragraphs=[
            [
                (
                    "Coded clinical entries in {product} use the SNOMED CT UK Edition, and "
                    "medicines are referenced through the NHS Dictionary of Medicines and "
                    "Devices.",
                    "{product} codes clinical entries with the SNOMED CT UK Edition and refers "
                    "to medicines using the NHS Dictionary of Medicines and Devices.",
                ),
                (
                    "Terminology content is refreshed from the national releases within thirty "
                    "days of publication.",
                    "We load each national terminology release within thirty days of its "
                    "publication.",
                ),
            ],
            [
                (
                    "The NHS number is the primary patient identifier throughout the solution, "
                    "verified against the Personal Demographics Service at the point of "
                    "registration and displayed in the national format.",
                    "Throughout the solution the NHS number is the primary identifier; it is "
                    "verified against the Personal Demographics Service when a patient is "
                    "registered and shown in the national format.",
                ),
            ],
        ],
    )
)

# --- Implementation -------------------------------------------------------------------------

_register(
    _c(
        id="im_plan",
        section="im",
        topic="implementation_and_onboarding",
        question=(
            "Provide an outline implementation plan showing the key phases, milestones and "
            "timescales from contract award to go-live."
        ),
        variants=(
            "Describe your proposed implementation approach and timeline, including the main "
            "phases and milestones to full go-live.",
            "Set out your implementation plan from award to live operation, with phases and "
            "indicative durations.",
        ),
        word_limit=600,
        weighting=7,
        mandatory=True,
        brief=(
            "Twelve-week plan: mobilisation, discovery and design, build and integration, "
            "testing, training, pilot go-live, full roll-out; named project manager; weekly "
            "reporting."
        ),
        paragraphs=[
            [
                (
                    "We propose a twelve-week implementation from contract award to first "
                    "go-live, led by a named project manager and following our established "
                    "delivery method.",
                    "Our implementation runs for twelve weeks from contract award to the first "
                    "go-live, under a named project manager and our standard delivery method.",
                ),
                (
                    "Weeks one and two are mobilisation: governance is agreed, the joint project "
                    "team is formed and the integration and information governance workstreams "
                    "start.",
                    "The first two weeks are mobilisation, during which governance is agreed, "
                    "the joint project team is stood up and the integration and information "
                    "governance workstreams begin.",
                ),
            ],
            [
                (
                    "Weeks three to five cover discovery and design, when we map current "
                    "workflows with clinical leads and agree the configuration of teams, task "
                    "types and referral pathways.",
                    "Discovery and design occupy weeks three to five: current workflows are "
                    "mapped with clinical leads and the configuration of teams, task types and "
                    "referral pathways is agreed.",
                ),
                (
                    "Weeks six to nine are build, integration and testing, including user "
                    "acceptance testing led by the Trust with our support.",
                    "Build, integration and testing run from week six to week nine and include "
                    "Trust-led user acceptance testing with our support.",
                ),
                (
                    "Training runs in weeks nine and ten, with a pilot go-live on two wards in "
                    "week eleven and Trust-wide roll-out from week twelve.",
                    "Training is delivered in weeks nine and ten, a two-ward pilot goes live in "
                    "week eleven and the Trust-wide roll-out starts in week twelve.",
                ),
            ],
            [
                (
                    "Progress is reported weekly against the plan and a formal go-live readiness "
                    "review, including clinical safety sign-off, precedes each go-live.",
                    "We report progress against the plan each week, and every go-live is "
                    "preceded by a formal readiness review that includes clinical safety "
                    "sign-off.",
                ),
            ],
        ],
    )
)

_register(
    _c(
        id="im_governance",
        section="im",
        topic="implementation_and_onboarding",
        question=(
            "Describe the project governance and risk management arrangements you will put in "
            "place for the implementation."
        ),
        variants=(
            "How will the implementation be governed, and how will risks and issues be "
            "managed and escalated?",
        ),
        word_limit=400,
        weighting=4,
        brief=(
            "Joint project board, weekly project meeting, RAID log, escalation route, "
            "change control, lessons learned."
        ),
        paragraphs=[
            [
                (
                    "Implementation is governed by a joint project board, chaired by the Trust's "
                    "senior responsible owner, that meets monthly to approve stage gates and "
                    "resolve escalated risks.",
                    "A joint project board chaired by the Trust's senior responsible owner meets "
                    "every month to approve stage gates and resolve risks escalated to it.",
                ),
                (
                    "Beneath the board a weekly project meeting, led by our project manager and "
                    "the Trust's project lead, tracks the plan, actions and dependencies.",
                    "A weekly project meeting led jointly by our project manager and the Trust's "
                    "project lead tracks the plan, actions and dependencies.",
                ),
            ],
            [
                (
                    "Risks, assumptions, issues and dependencies are held in a shared log with an "
                    "owner, a rating and a mitigation for each, and any risk rated high is "
                    "escalated to the project board within two working days.",
                    "A shared log records risks, assumptions, issues and dependencies with an "
                    "owner, rating and mitigation, and high-rated risks reach the project board "
                    "within two working days.",
                ),
                (
                    "Changes to scope follow a written change control process with impact "
                    "assessment before approval.",
                    "Scope changes go through written change control, with an impact assessment "
                    "completed before approval.",
                ),
            ],
        ],
    )
)

# --- Training and support -------------------------------------------------------------------

_register(
    _c(
        id="tr_training",
        section="tr",
        topic="training_and_support",
        question=(
            "Describe your approach to training clinical and administrative staff, including "
            "the training materials and any ongoing training provision."
        ),
        variants=(
            "How will you train our staff before go-live, and what training is available "
            "afterwards for new starters?",
        ),
        word_limit=400,
        weighting=4,
        brief=(
            "Train-the-trainer model, role-based e-learning, floor-walking at go-live, quick "
            "reference guides, training environment, new-starter modules."
        ),
        paragraphs=[
            [
                (
                    "We use a train-the-trainer model: we train a cohort of Trust super-users in "
                    "half-day sessions, and they cascade training to their teams with our "
                    "materials and support.",
                    "Our train-the-trainer approach equips a cohort of Trust super-users in "
                    "half-day sessions; they then cascade training to colleagues using our "
                    "materials, with our support.",
                ),
                (
                    "Role-based e-learning modules of twenty to thirty minutes are available to "
                    "all staff before go-live, and completion is tracked and reported to the "
                    "project team.",
                    "All staff have access to role-based e-learning modules lasting twenty to "
                    "thirty minutes ahead of go-live, and completion is tracked and reported to "
                    "the project team.",
                ),
            ],
            [
                (
                    "During each go-live week our trainers floor-walk on the wards alongside the "
                    "super-users.",
                    "In the week of each go-live our trainers are present on the wards, "
                    "floor-walking with the super-users.",
                ),
                (
                    "A training environment with realistic test data remains available for the "
                    "life of the contract, and new starters complete the e-learning modules as "
                    "part of Trust induction.",
                    "For the whole contract the Trust keeps a training environment populated "
                    "with realistic test data, and new starters take the e-learning modules "
                    "during induction.",
                ),
            ],
        ],
    )
)

_register(
    _c(
        id="tr_support_sla",
        section="tr",
        topic="service_levels",
        question=(
            "Describe your support arrangements and service levels, including service desk "
            "hours, incident priority definitions and response and resolution targets."
        ),
        variants=(
            "Set out your service desk provision and the service levels offered for "
            "availability, incident response and resolution.",
        ),
        word_limit=500,
        weighting=6,
        mandatory=True,
        brief=(
            "24/7 service desk, priority definitions, response and resolution targets by "
            "priority, availability target, monthly service report, service credits."
        ),
        paragraphs=[
            [
                (
                    "Our service desk is available twenty-four hours a day, every day, by "
                    "telephone, email and the customer portal.",
                    "Support is available around the clock, every day of the year, through "
                    "telephone, email and our customer portal.",
                ),
                (
                    "Incidents are assigned one of four priorities: priority one is a total loss "
                    "of service or a clinical safety risk, priority two a major degradation, "
                    "priority three a fault affecting a small number of users and priority four "
                    "a cosmetic issue or service request.",
                    "We use four incident priorities: priority one for total loss of service or "
                    "a clinical safety risk, priority two for major degradation, priority three "
                    "for faults affecting few users and priority four for cosmetic issues and "
                    "service requests.",
                ),
            ],
            [
                (
                    "Priority one incidents receive a response within {p1_response} and a "
                    "resolution or workaround within {p1_resolution}.",
                ),
                (
                    "Priority two incidents are responded to within one hour and resolved within "
                    "one working day; priority three within four hours and five working days; "
                    "priority four within one working day and by the next scheduled release.",
                    "For priority two the targets are a one-hour response and resolution within "
                    "one working day; for priority three four hours and five working days; for "
                    "priority four one working day and the next scheduled release.",
                ),
                (
                    "The service availability target is {sla_availability}, measured monthly and "
                    "excluding agreed maintenance windows, and service credits apply when it is "
                    "missed.",
                ),
            ],
            [
                (
                    "A monthly service report covers availability, incident volumes against "
                    "target and the status of open problems, and is reviewed at the quarterly "
                    "service review with the Trust.",
                    "Each month the Trust receives a service report on availability, incident "
                    "volumes against target and open problems, and we review it together at the "
                    "quarterly service review.",
                ),
            ],
        ],
    )
)

# --- Commercial -----------------------------------------------------------------------------

_register(
    _c(
        id="co_pricing_model",
        section="co",
        topic="commercial_and_pricing",
        question=(
            "Describe your commercial model, including how charges are structured, the "
            "proposed contract term and any dependencies on user or bed numbers. Do not include "
            "prices in this response."
        ),
        variants=(
            "Explain the structure of your charges and the contract term you propose, without "
            "stating prices, which must be entered in the pricing schedule only.",
        ),
        word_limit=400,
        weighting=4,
        brief=(
            "Annual subscription per bed band, one-off implementation charge, no per-user "
            "fees, three-year term with two optional extensions, quarterly invoicing in "
            "arrears, no prices stated."
        ),
        paragraphs=[
            [
                (
                    "Our commercial model is an annual subscription set by bed band, so the "
                    "charge is predictable and does not rise as more staff use the service.",
                    "We charge an annual subscription determined by bed band, which keeps the "
                    "charge predictable and independent of the number of staff using the "
                    "service.",
                ),
                (
                    "There are no per-user fees, no charges for application programming "
                    "interface access and no charge for the training environment.",
                    "We levy no per-user fees, no fees for application programming interface "
                    "access and nothing for the training environment.",
                ),
                (
                    "A one-off implementation charge covers project management, configuration, "
                    "integration and training, and is invoiced against milestones.",
                    "Project management, configuration, integration and training are covered by "
                    "a single one-off implementation charge, invoiced against milestones.",
                ),
            ],
            [
                (
                    "We propose a three-year initial term with two optional one-year extensions "
                    "at the Trust's discretion, and subscription charges are fixed for the "
                    "initial term.",
                    "The proposed term is three years with two one-year extensions exercisable "
                    "by the Trust, and the subscription is fixed for the initial term.",
                ),
                (
                    "Subscriptions are invoiced quarterly in arrears on thirty-day payment terms, "
                    "and all charges are set out in the pricing schedule rather than in this "
                    "response.",
                    "Invoicing is quarterly in arrears with thirty-day payment terms, and the "
                    "charges themselves appear only in the pricing schedule.",
                ),
            ],
        ],
    )
)

_register(
    _c(
        id="co_contract_exit",
        section="co",
        topic="commercial_and_pricing",
        question=(
            "Describe your exit and transition arrangements at the end of the contract, "
            "including data return and support for migration to a successor supplier."
        ),
        variants=(
            "What exit management support do you provide, and how will data be returned or "
            "transferred to a successor supplier?",
        ),
        word_limit=300,
        weighting=3,
        brief=(
            "Exit plan agreed within three months of award, data export in open formats, "
            "parallel running support, knowledge transfer, deletion certificate."
        ),
        paragraphs=[
            [
                (
                    "An exit plan is agreed with the Trust within three months of contract award "
                    "and reviewed annually, so that exit is planned rather than improvised.",
                    "Within three months of award we agree an exit plan with the Trust and review "
                    "it every year, so that exit is a planned activity.",
                ),
                (
                    "On exit we provide a complete export of the Trust's data in open, documented "
                    "formats and make our integration specifications available to the successor "
                    "supplier.",
                    "At exit the Trust receives a full data export in open, documented formats, "
                    "and the successor supplier is given our integration specifications.",
                ),
            ],
            [
                (
                    "We support up to three months of parallel running and provide knowledge "
                    "transfer sessions for the Trust and the incoming supplier at no additional "
                    "charge.",
                    "Parallel running is supported for up to three months, and knowledge "
                    "transfer sessions for the Trust and the incoming supplier are included at "
                    "no extra charge.",
                ),
                (
                    "Once the Trust confirms the data has been received, all copies are deleted "
                    "and a certificate of deletion is issued.",
                    "After the Trust confirms receipt of its data we delete every copy and issue "
                    "a certificate of deletion.",
                ),
            ],
        ],
    )
)

# --- Social value ---------------------------------------------------------------------------

_register(
    _c(
        id="sv_carbon",
        section="sv",
        topic="social_value",
        question=(
            "Describe your commitment to achieving net zero and provide details of your Carbon "
            "Reduction Plan."
        ),
        variants=(
            "Set out your organisation's net zero commitment and summarise your published "
            "Carbon Reduction Plan.",
            "How is your organisation reducing its carbon emissions, and what does your Carbon "
            "Reduction Plan commit you to?",
        ),
        word_limit=400,
        weighting=5,
        mandatory=True,
        brief=(
            "Carbon Reduction Plan published to PPN 06/21, net zero by 2040, scope 1, 2 and "
            "relevant scope 3 baseline, actions: renewable hosting, travel policy, remote-first."
        ),
        paragraphs=[
            [
                (
                    "{company} has published a Carbon Reduction Plan in the format required by "
                    "Procurement Policy Note 06/21 and has committed to net zero across scope "
                    "one, scope two and the required scope three categories by 2040.",
                    "Our Carbon Reduction Plan follows the Procurement Policy Note 06/21 format "
                    "and commits {company} to net zero for scope one, scope two and the "
                    "required scope three categories by 2040.",
                ),
                (
                    "The plan is signed by the board, reviewed annually and published on our "
                    "website.",
                    "It is board-signed, reviewed each year and published on our website.",
                ),
            ],
            [
                (
                    "Our hosting runs in regions powered by renewable energy, our offices use "
                    "renewable electricity tariffs and we operate a remote-first working policy "
                    "that has reduced commuting emissions by more than half since our baseline "
                    "year.",
                    "We host in regions supplied with renewable energy, our offices are on "
                    "renewable electricity tariffs and remote-first working has cut commuting "
                    "emissions by over half against our baseline year.",
                ),
                (
                    "Business travel follows a rail-first policy and we report emissions to "
                    "customers annually alongside the service review.",
                    "A rail-first travel policy applies to all business travel, and we report our "
                    "emissions to customers each year at the service review.",
                ),
            ],
        ],
    )
)

_register(
    _c(
        id="sv_local_employment",
        section="sv",
        topic="social_value",
        question=(
            "Describe the social value you will deliver through this contract in relation to "
            "local employment, skills and apprenticeships."
        ),
        variants=(
            "What commitments do you make to local employment, apprenticeships and skills "
            "development as part of this contract?",
        ),
        word_limit=400,
        weighting=5,
        brief=(
            "Apprenticeship places, work experience with local colleges, digital skills "
            "sessions for Trust staff, recruitment from the local area, reporting."
        ),
        paragraphs=[
            [
                (
                    "Through this contract we will create two digital apprenticeship places in "
                    "the Trust's area within the first year and offer paid work experience to "
                    "students from local further education colleges.",
                    "We commit to two digital apprenticeship places in the Trust's area during "
                    "the first contract year and to paid work experience placements for students "
                    "from local further education colleges.",
                ),
                (
                    "Our implementation and support roles for the contract are advertised locally "
                    "first.",
                    "Contract implementation and support roles are advertised in the local area "
                    "before anywhere else.",
                ),
            ],
            [
                (
                    "We will run free digital skills sessions for Trust staff twice a year, "
                    "covering data literacy and the safe use of digital tools.",
                    "Twice a year we will deliver free digital skills sessions for Trust staff on "
                    "data literacy and the safe use of digital tools.",
                ),
                (
                    "Social value delivery is reported quarterly against the agreed measures and "
                    "reviewed at the contract review meeting.",
                    "Each quarter we report social value delivery against the agreed measures "
                    "for review at the contract review meeting.",
                ),
            ],
        ],
    )
)

# --- Company and experience -----------------------------------------------------------------

_register(
    _c(
        id="cx_company_overview",
        section="cx",
        topic="company_and_experience",
        question=(
            "Provide an overview of your organisation, including its legal status, size and "
            "relevant experience of delivering similar solutions to NHS organisations."
        ),
        variants=(
            "Describe your organisation: legal form, company registration, headcount and "
            "experience of comparable NHS deployments.",
            "Give a brief profile of your company, including registration details, number of "
            "staff and relevant NHS experience.",
        ),
        word_limit=400,
        weighting=4,
        brief=(
            "Limited company, registration number, founded year, registered office, "
            "headcount, NHS customer count, product description, insurance held."
        ),
        paragraphs=[
            [
                (
                    "{company} is a private limited company registered in England and Wales "
                    "under company number {company_number}, founded in {founded} and based at "
                    "{registered_office}.",
                ),
                (
                    "We employ {headcount} staff, of whom more than half work in product "
                    "engineering and clinical safety.",
                ),
                (
                    "Our sole product is {product}, {product_desc}, which is in live use across "
                    "eleven NHS trusts and integrated care systems.",
                    "{product}, {product_desc}, is our only product and is live in eleven NHS "
                    "trusts and integrated care systems.",
                ),
            ],
            [
                (
                    "Recent deployments include a community trust where referral turnaround fell "
                    "by a third within six months, and an acute trust where handover incidents "
                    "reported to the safety team halved in the first year.",
                    "In a recent community trust deployment referral turnaround fell by a third "
                    "inside six months, and in an acute trust handover incidents reported to the "
                    "safety team halved in the first year.",
                ),
                (
                    "We hold public liability insurance of {pl_cover}, professional indemnity "
                    "insurance of {pi_cover}, employer's liability insurance of {el_cover} and "
                    "cyber insurance of {cyber_cover}.",
                ),
            ],
        ],
    )
)

# --- Pack-only concepts (no past answer exists) ---------------------------------------------

for _concept in (
    _c(
        id="cs_attach_safety_case",
        section="cs",
        topic="clinical_safety",
        question="Attach your current clinical safety case report for the proposed solution.",
        response_type="attachment",
        word_limit=None,
        weighting=None,
        mandatory=True,
    ),
    _c(
        id="ig_yn_dspt",
        section="ig",
        topic="information_governance",
        question=(
            "Has your organisation achieved Standards Met in the most recent Data Security and "
            "Protection Toolkit assessment? (Yes/No)"
        ),
        response_type="yes_no",
        word_limit=None,
        weighting=None,
        mandatory=True,
    ),
    _c(
        id="is_yn_ce_plus",
        section="is",
        topic="information_security",
        question=(
            "Does your organisation hold current Cyber Essentials Plus certification? (Yes/No)"
        ),
        response_type="yes_no",
        word_limit=None,
        weighting=None,
        mandatory=True,
    ),
    _c(
        id="is_incident_response",
        section="is",
        topic="information_security",
        question=(
            "Describe your security incident response process, including how and when the "
            "Trust would be notified of a security incident affecting its data."
        ),
        word_limit=400,
        weighting=4,
    ),
    _c(
        id="is_bc_dr",
        section="is",
        topic="service_levels",
        question=(
            "Describe your business continuity and disaster recovery arrangements, including "
            "recovery time and recovery point objectives and the frequency of testing."
        ),
        word_limit=400,
        weighting=4,
    ),
    _c(
        id="io_accessibility",
        section="io",
        topic="product_functionality",
        question=(
            "Confirm the solution's conformance with WCAG 2.2 AA and describe how "
            "accessibility is tested."
        ),
        word_limit=300,
        weighting=3,
    ),
    _c(
        id="im_data_migration",
        section="im",
        topic="implementation_and_onboarding",
        question=(
            "Describe your approach to migrating existing task lists and open referrals from "
            "the Trust's current systems, including validation and cut-over."
        ),
        word_limit=400,
        weighting=4,
    ),
    _c(
        id="im_user_research",
        section="im",
        topic="product_functionality",
        question=(
            "Describe how clinicians and patients are involved in the design and ongoing "
            "development of the solution."
        ),
        word_limit=300,
        weighting=3,
    ),
    _c(
        id="tr_mi_reporting",
        section="tr",
        topic="service_levels",
        question=(
            "Describe the management information and reporting available to the Trust, "
            "including operational dashboards and data extracts."
        ),
        word_limit=300,
        weighting=3,
    ),
    _c(
        id="co_pricing_schedule",
        section="co",
        topic="commercial_and_pricing",
        question=(
            "Complete the pricing schedule at Appendix C, stating all charges for the initial "
            "term and each optional extension year."
        ),
        response_type="pricing",
        word_limit=None,
        weighting=30,
        mandatory=True,
    ),
    _c(
        id="co_optional_charges",
        section="co",
        topic="commercial_and_pricing",
        question=(
            "State the charges for any optional modules or services not included in the core "
            "subscription, using the format in Appendix C."
        ),
        response_type="pricing",
        word_limit=None,
        weighting=None,
    ),
    _c(
        id="co_yn_terms",
        section="co",
        topic="commercial_and_pricing",
        question=(
            "Do you accept the Trust's terms and conditions, as set out in the Invitation to "
            "Tender, without amendment? (Yes/No)"
        ),
        response_type="yes_no",
        word_limit=None,
        weighting=None,
        mandatory=True,
    ),
    _c(
        id="co_yn_nhs_contract",
        section="co",
        topic="commercial_and_pricing",
        question=(
            "Do you accept the NHS Standard Contract as the basis for this agreement? (Yes/No)"
        ),
        response_type="yes_no",
        word_limit=None,
        weighting=None,
        mandatory=True,
    ),
    _c(
        id="cx_yn_insurance",
        section="cx",
        topic="company_and_experience",
        question=(
            "Do you hold, or will you hold before contract award, employer's liability "
            "insurance of at least £10 million and public liability insurance of at least "
            "£10 million? (Yes/No)"
        ),
        response_type="yes_no",
        word_limit=None,
        weighting=None,
        mandatory=True,
    ),
    _c(
        id="cx_financial_standing",
        section="cx",
        topic="company_and_experience",
        question=(
            "Provide details of your financial standing, including turnover for the last two "
            "financial years and confirmation that audited accounts are available on request."
        ),
        word_limit=300,
        weighting=3,
    ),
    _c(
        id="cx_subcontractors",
        section="cx",
        topic="company_and_experience",
        question=(
            "Provide details of any subcontractors or consortium members you propose to use in "
            "delivering this contract and the proportion of the contract each will deliver."
        ),
        word_limit=300,
        weighting=2,
    ),
    _c(
        id="sv_modern_slavery",
        section="sv",
        topic="social_value",
        question=(
            "Describe the steps your organisation takes to ensure there is no modern slavery "
            "in its business or supply chain."
        ),
        word_limit=300,
        weighting=3,
    ),
    _c(
        id="sv_wellbeing",
        section="sv",
        topic="social_value",
        question=(
            "Describe how you will support the health and wellbeing of your workforce and "
            "contribute to reducing health inequalities in the Trust's area."
        ),
        word_limit=400,
        weighting=4,
    ),
):
    _register(_concept)


# ---------------------------------------------------------------------------------------------
# Submissions and the pack
# ---------------------------------------------------------------------------------------------

LAYOUT_ADJACENT_CELLS = "adjacent_cells"
LAYOUT_HEADING_ANSWER = "heading_answer"
LAYOUT_NUMBERED_FORM = "numbered_form"
LAYOUTS: tuple[str, ...] = (LAYOUT_ADJACENT_CELLS, LAYOUT_HEADING_ANSWER, LAYOUT_NUMBERED_FORM)


@dataclass(frozen=True)
class SubmissionSpec:
    key: str
    filename: str
    layout: str
    buyer: str
    tender_title: str
    tender_reference: str
    submission_date: date
    concept_ids: tuple[str, ...]
    held_out: bool = False
    # Concepts whose answer must be a light rewrite of an earlier submission's answer, so
    # deduplication has clusters to find: {concept_id: earlier submission key}.
    near_identical_to: dict[str, str] = field(default_factory=dict)
    # Which question phrasing each concept uses in this submission (index into variants).
    question_variant: int = 0


SUBMISSIONS: tuple[SubmissionSpec, ...] = (
    SubmissionSpec(
        key="westmoor_2024",
        filename="past_submission_westmoor_icb_2024.docx",
        layout=LAYOUT_ADJACENT_CELLS,
        buyer="NHS Westmoor Integrated Care Board",
        tender_title="Digital Clinical Communication Platform",
        tender_reference="WICB-2024-117",
        submission_date=date(2024, 6, 14),
        concept_ids=(
            "cs_dcb0129",
            "cs_officer",
            "ig_dspt",
            "ig_dpo",
            "is_iso27001",
            "is_ce_plus",
            "is_access_control",
            "io_standards",
            "io_integration_epr",
            "im_plan",
            "tr_support_sla",
            "co_pricing_model",
            "sv_carbon",
            "cx_company_overview",
        ),
        question_variant=0,
    ),
    SubmissionSpec(
        key="harbourside_2025",
        filename="past_submission_harbourside_fT_2025.docx",
        layout=LAYOUT_HEADING_ANSWER,
        buyer="Harbourside University Hospitals NHS Foundation Trust",
        tender_title="Electronic Task Management and Clinical Noting Solution",
        tender_reference="HUH/ITT/2025/032",
        submission_date=date(2025, 2, 21),
        concept_ids=(
            "cs_dcb0129",
            "cs_hazard_log",
            "ig_dspt",
            "ig_dpia",
            "ig_data_residency",
            "is_iso27001",
            "is_pen_testing",
            "io_standards",
            "io_snomed",
            "im_governance",
            "tr_training",
            "co_contract_exit",
            "sv_carbon",
            "sv_local_employment",
        ),
        near_identical_to={
            "cs_dcb0129": "westmoor_2024",
            "ig_dspt": "westmoor_2024",
            "is_iso27001": "westmoor_2024",
            "io_standards": "westmoor_2024",
            "sv_carbon": "westmoor_2024",
        },
        question_variant=1,
    ),
    SubmissionSpec(
        key="northern_fells_2025",
        filename="past_submission_northern_fells_2025_heldout.docx",
        layout=LAYOUT_NUMBERED_FORM,
        buyer="Northern Fells Community Health NHS Trust",
        tender_title="Community Clinical Communication and Referral Platform",
        tender_reference="NFCH-2025-ITT-09",
        submission_date=date(2025, 7, 18),
        concept_ids=(
            "cs_dcb0129",
            "cs_officer",
            "cs_hazard_log",
            "cs_dcb0160_support",
            "ig_dspt",
            "ig_dpo",
            "ig_retention",
            "is_iso27001",
            "is_ce_plus",
            "is_access_control",
            "io_integration_epr",
            "im_plan",
            "tr_support_sla",
            "cx_company_overview",
        ),
        held_out=True,
        question_variant=2,
    ),
)

HELD_OUT_KEY = "northern_fells_2025"

# The question pack: every held-out concept, then further concepts. Order is by section.
QUESTION_PACK_FILENAME = "question_pack_northern_fells_2025.xlsx"
QUESTION_PACK_CONCEPT_IDS: tuple[str, ...] = (
    # Clinical safety
    "cs_dcb0129",
    "cs_officer",
    "cs_hazard_log",
    "cs_dcb0160_support",
    "cs_attach_safety_case",
    # Information governance
    "ig_dspt",
    "ig_yn_dspt",
    "ig_dpo",
    "ig_dpia",
    "ig_data_residency",
    "ig_retention",
    # Information security
    "is_iso27001",
    "is_ce_plus",
    "is_yn_ce_plus",
    "is_access_control",
    "is_pen_testing",
    "is_incident_response",
    "is_bc_dr",
    # Interoperability
    "io_standards",
    "io_integration_epr",
    "io_snomed",
    "io_accessibility",
    # Implementation
    "im_plan",
    "im_governance",
    "im_data_migration",
    "im_user_research",
    # Training and support
    "tr_training",
    "tr_support_sla",
    "tr_mi_reporting",
    # Commercial
    "co_pricing_model",
    "co_contract_exit",
    "co_pricing_schedule",
    "co_optional_charges",
    "co_yn_terms",
    "co_yn_nhs_contract",
    # Social value
    "sv_carbon",
    "sv_local_employment",
    "sv_modern_slavery",
    "sv_wellbeing",
    # Company and experience
    "cx_company_overview",
    "cx_yn_insurance",
    "cx_financial_standing",
    "cx_subcontractors",
)

# Yes/no and attachment questions that an existing free-text answer nevertheless answers.
PACK_COUNTERPART_ALIASES: dict[str, str] = {
    "ig_yn_dspt": "ig_dspt",
    "is_yn_ce_plus": "is_ce_plus",
}

# Pack rows use a phrasing distinct from the held-out submission's where one exists.
PACK_QUESTION_VARIANT = 1


# ---------------------------------------------------------------------------------------------
# Reference documents
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReferenceSpec:
    key: str
    filename: str
    doc_kind: str
    effective_date: date
    certificate_number: str
    valid_until: str
    issue_note: str


REFERENCE_DOCUMENTS: tuple[ReferenceSpec, ...] = (
    ReferenceSpec(
        key="iso_2024",
        filename="reference_iso27001_certificate_2024.docx",
        doc_kind="iso_27001",
        effective_date=date(2024, 3, 12),
        certificate_number="NHI-27001-0417",
        valid_until="11 March 2027",
        issue_note=(
            "This is the initial certificate of the current three-year certification cycle, "
            "issued following the stage two certification audit completed in February 2024."
        ),
    ),
    ReferenceSpec(
        key="iso_2025",
        filename="reference_iso27001_certificate_2025.docx",
        doc_kind="iso_27001",
        effective_date=date(2025, 3, 9),
        certificate_number="NHI-27001-0582",
        valid_until="11 March 2027",
        issue_note=(
            "This certificate re-issues certificate NHI-27001-0417 following the first "
            "surveillance audit, completed in February 2025, and an extension of scope to the "
            "Manchester delivery office. It replaces the earlier certificate."
        ),
    ),
)

REFERENCE_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Scope of certification",
        (
            "The information security management system covers the design, development, "
            "hosting, operation and support of {product}, {product_desc}, and the corporate "
            "functions that support it, including human resources, finance and the service "
            "desk.",
            "The system applies to all information assets owned or processed by {company} on "
            "behalf of its customers, including personal data processed as a data processor "
            "for NHS organisations.",
            "This is in accordance with the Statement of Applicability version {soa_version} "
            "dated {soa_date}.",
        ),
    ),
    (
        "Certified sites",
        (
            "Head office: {registered_office}, England.",
            "{sites_note}",
            "Cloud hosting environments in two United Kingdom regions are within scope as "
            "externally provided processes, services and functions under clause 8.1 of the "
            "standard.",
        ),
    ),
    (
        "Certification cycle and surveillance",
        (
            "Certification is granted for a three-year cycle ending on {valid_until}, subject "
            "to satisfactory annual surveillance audits.",
            "{cycle_note}",
            "The certificate remains the property of {cert_body} and may be withdrawn if the "
            "conditions of certification are not maintained.",
        ),
    ),
    (
        "Certification body statement",
        (
            "{cert_body} confirms that the management system described above has been audited "
            "against the requirements of ISO/IEC 27001:2022 and found to conform.",
            "The audit assessed the organisation's risk assessment and treatment, its "
            "Annex A control selection and the operation of the controls selected, including "
            "access control, cryptography, supplier relationships and incident management.",
            "Verification of this certificate may be requested from {cert_body} quoting the "
            "certificate number.",
        ),
    ),
)

REFERENCE_VARIABLES: dict[str, dict[str, str]] = {
    "iso_2024": {
        "soa_version": "4.0",
        "soa_date": "5 February 2024",
        "sites_note": "No additional sites are within scope at the date of issue.",
        "cycle_note": (
            "The first surveillance audit is scheduled for February 2025 and the second for "
            "February 2026."
        ),
    },
    "iso_2025": {
        "soa_version": "4.2",
        "soa_date": "3 February 2025",
        "sites_note": (
            "Delivery office: Manchester, England, added to scope at the first surveillance "
            "audit."
        ),
        "cycle_note": (
            "The first surveillance audit was completed in February 2025 with no major "
            "non-conformities; the second is scheduled for February 2026."
        ),
    },
}
