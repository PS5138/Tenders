export const REVIEWER_ROLES = ['sme', 'approver', 'reviewer'] as const;
export type ReviewerRole = (typeof REVIEWER_ROLES)[number];
export const REVIEWER_ROLE_LABELS: Record<ReviewerRole, string> = {
  sme: 'SME reviewer',
  approver: 'Approver',
  reviewer: 'Reviewer',
};
