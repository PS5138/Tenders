/**
 * Whose turn a question is, for My tasks. This only decides what is listed under "Your turn";
 * it never blocks anyone, because any member may move a question's status.
 *
 * The order is owner, then SME reviewer, then approver. A stage with nobody assigned is skipped,
 * and with no reviewers at all the owner carries the question through to approval.
 */
import type { ReviewerRole } from './review-roles';

export type TaskQuestion = {
  status: string;
  assignee?: string | null;
  needs_review?: boolean;
  current_answer?: { support_summary: { needs_attention?: number | null } } | null;
};
export type Assigned = { sme: string[]; approver: string[]; reviewer: string[] };
export type Turn = { turn: boolean; role: 'owner' | ReviewerRole; reason: string };

const list = (names: string[]) => (names.length > 2 ? `${names.slice(0, 2).join(', ')} and others` : names.join(' and '));

/** Who the question is waiting on, and why, in plain words. */
export function stage(q: TaskQuestion, assigned: Assigned): { waitingOn: 'owner' | 'sme' | 'approver'; reason: string } {
  const attention = q.current_answer?.support_summary.needs_attention ?? 0;
  if (q.status === 'not_started') return { waitingOn: 'owner', reason: 'Write or generate the answer' };
  if (q.needs_review) return { waitingOn: 'owner', reason: 'Resolve the items marked as needing review' };
  if (q.status === 'ai_draft' || q.status === 'writer_edited') {
    if (assigned.sme.length) return { waitingOn: 'sme', reason: 'Verify the answer as SME reviewer' };
    if (q.status === 'ai_draft') return { waitingOn: 'owner', reason: 'Review and edit the AI draft' };
    if (attention) return { waitingOn: 'owner', reason: `${attention} ${attention === 1 ? 'sentence needs' : 'sentences need'} a source or confirmation` };
    if (assigned.approver.length) return { waitingOn: 'approver', reason: 'Approve the answer' };
    return { waitingOn: 'owner', reason: 'Move the answer on for approval' };
  }
  if (q.status === 'sme_verified') {
    if (assigned.approver.length) return { waitingOn: 'approver', reason: 'Approve the answer' };
    return { waitingOn: 'owner', reason: 'Approve the answer' };
  }
  return { waitingOn: 'owner', reason: 'Approved' };
}

/**
 * The task for one person on one question. Generic reviewers are asked to look at any drafted
 * answer that is not yet approved; they are never the stage a question waits on.
 */
export function taskFor(q: TaskQuestion, me: { owner: boolean; roles: ReviewerRole[] }, assigned: Assigned): Turn[] {
  const now = stage(q, assigned);
  const turns: Turn[] = [];
  const waiting = (who: 'owner' | 'sme' | 'approver') =>
    who === 'owner' ? `Waiting on the owner${q.assignee ? `, ${q.assignee}` : ''}` : `Waiting on ${who === 'sme' ? 'SME review' : 'approval'} by ${list(assigned[who])}`;
  if (me.owner) turns.push({ turn: now.waitingOn === 'owner', role: 'owner', reason: now.waitingOn === 'owner' ? now.reason : waiting(now.waitingOn) });
  for (const role of me.roles) {
    if (role === 'reviewer') {
      const ready = q.status !== 'not_started' && q.status !== 'approved';
      turns.push({ turn: ready, role, reason: ready ? 'Review the answer and comment' : 'Waiting for a first answer' });
    } else {
      const mine = now.waitingOn === role;
      turns.push({ turn: mine, role, reason: mine ? now.reason : waiting(now.waitingOn) });
    }
  }
  return turns;
}
