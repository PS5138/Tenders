// `PATCH /questions/{id}` with a status the review gates refuse still applies the other fields
// (assignee, compliance class, compliant-by): the backend commits them and answers 409 with
// `{detail, to, blockers, question}`, where `question` is the row as saved. Notifications follow
// what was saved, so the refused status is dropped and the assignee kept only if it landed.

/** The part of a PATCH body the backend applied despite refusing its status; null when nothing to notify. */
export function appliedPartOfRefusedPatch(
  body: Record<string, unknown>,
  refusal: unknown,
): Record<string, unknown> | null {
  if (!refusal || typeof refusal !== 'object') return null;
  const question = (refusal as { question?: unknown }).question;
  if (!question || typeof question !== 'object') return null;
  if (!('assignee' in body)) return null;
  const saved = (question as { assignee?: unknown }).assignee ?? null;
  if (saved !== (body.assignee ?? null)) return null;
  return { assignee: body.assignee };
}
