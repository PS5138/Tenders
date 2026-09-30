import type { Tone } from '@/components/ui/badge';

export const REVIEW_STATE: Record<string, { label: string; tone: Tone }> = {
  draft: { label: 'Draft', tone: 'neutral' },
  in_review: { label: 'In review', tone: 'blue' },
  changes_requested: { label: 'Changes requested', tone: 'amber' },
  approved: { label: 'Approved', tone: 'green' },
};

export const PARSE_STATUS: Record<string, { label: string; tone: Tone }> = {
  uploaded: { label: 'Queued', tone: 'neutral' },
  extracting: { label: 'Extracting', tone: 'blue' },
  ready: { label: 'Ready', tone: 'green' },
  failed: { label: 'Failed', tone: 'red' },
  unsupported: { label: 'Unsupported', tone: 'red' },
};

export const ROLE_LABELS: Record<string, string> = {
  background: 'Background',
  specification: 'Specification',
  requirements: 'Requirements',
  instructions: 'Instructions',
  evaluation_criteria: 'Evaluation criteria',
  response_form: 'Response form',
  reference: 'Reference',
};

export const KIND_LABELS: Record<string, string> = {
  requirement: 'Requirement',
  question: 'Question',
  form_field: 'Form field',
  deadline: 'Deadline',
  submission_instruction: 'Submission instruction',
  evaluation_criterion: 'Evaluation criterion',
  attachment: 'Attachment',
};

export const FORMAT_LABELS: Record<string, string> = {
  generated_docx: 'Generated DOCX',
  form_docx: 'Buyer Word form',
  form_xlsx: 'Buyer Excel form',
  attachment: 'Attachment',
  other: 'Other',
};

export const SOURCE_KIND_LABELS: Record<string, string> = {
  policy: 'Policy',
  procedure: 'Procedure',
  product_documentation: 'Product documentation',
  certification: 'Certification',
  delivery_guide: 'Delivery guide',
  historical_response: 'Historical response',
  other: 'Other',
};

export const SOURCE_REVIEW: Record<string, { label: string; tone: Tone }> = {
  unreviewed: { label: 'Not reviewed', tone: 'amber' },
  approved: { label: 'Approved', tone: 'green' },
  needs_review: { label: 'Needs review', tone: 'amber' },
  rejected: { label: 'Rejected', tone: 'red' },
};

export const EVIDENCE_ISSUE_LABELS: Record<string, string> = {
  conflict: 'Conflicting sources',
  missing_evidence: 'Missing evidence',
  expired_source: 'Expired source',
  unapproved_source: 'Unapproved source',
  scope_mismatch: 'Wrong product or market',
  unsupported_claim: 'Unsupported claim',
  contradicted_claim: 'Contradicted claim',
  buyer_input: 'Buyer input needed',
  missing_input: 'Missing input',
  other: 'Other issue',
};

export const OPERATION_LABELS: Record<string, string> = {
  parse_document: 'Extracting text',
  index_passages: 'Indexing for search',
  analyse_tender_pack: 'Analysing the specification',
  retrieve_evidence: 'Searching evidence',
  judge_retrieval: 'Assessing evidence',
  draft_response: 'Drafting',
  check_response: 'Checking the answer',
  judge_response: 'Assessing the answer',
  generate_export: 'Generating files',
};
