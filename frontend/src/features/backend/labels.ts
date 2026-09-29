/** Readable names for backend enumerations. Anything not listed is sentence-cased. */
const LABELS: Record<string, string> = {
  not_started: 'Not started',
  ai_draft: 'AI draft',
  writer_edited: 'Writer edited',
  sme_verified: 'SME verified',
  approved: 'Approved',
  covered: 'Covered',
  partial: 'Partial',
  new: 'New',
  question_pack: 'Question pack',
  specification: 'Specification',
  clarification_log: 'Clarification log',
  contract_terms: 'Contract terms',
  past_submission: 'Past submission',
  reference: 'Reference document',
  tender_document: 'Tender document',
  qa_pair: 'question and answer pairs',
  chunk: 'reference passages',
  promoted_answer: 'approved answers',
  free_text: 'Free text',
  yes_no: 'Yes or no',
  procurement_act: 'Procurement Act 2023',
  psr: 'Provider Selection Regime',
  pcr_2015: 'Public Contracts Regulations 2015',
  human_authored: 'Written by a person',
  ingest_document: 'Processing document',
  extract_questions: 'Finding questions',
  triage_tender: 'Checking library coverage',
  draft_all: 'Drafting answers',
  queued: 'Queued',
  parsing: 'Reading',
  classifying: 'Classifying',
  extracting: 'Extracting',
  embedding: 'Indexing',
  linking: 'Linking',
  ready: 'Ready',
  failed: 'Failed',
  open: 'Open',
  submitted: 'Submitted',
  archived: 'Archived',
  won: 'Won',
  lost: 'Lost',
  iso_27001: 'ISO 27001',
  dspt_confirmation: 'DSPT confirmation',
  cyber_essentials_plus: 'Cyber Essentials Plus',
  sme: 'SME reviewer',
};

export function label(value: string | null | undefined): string {
  if (!value) return '';
  const known = LABELS[value];
  if (known) return known;
  const text = value.replaceAll('_', ' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}
