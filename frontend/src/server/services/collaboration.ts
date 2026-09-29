import 'server-only';
import { randomUUID } from 'node:crypto';
import { inWorkspace, assertActiveMembers } from './context';
import { AppError, notFound } from '../errors';
import { backendForWorkspace } from '../backend/client';
import type { Tx } from '../db';

import type { ReviewerRole } from '@/lib/review-roles';
import { mentionedNames } from '@/lib/mentions';
export { REVIEWER_ROLES, REVIEWER_ROLE_LABELS, type ReviewerRole } from '@/lib/review-roles';

export type ReviewerRow = { questionId: string; userId: string; displayName: string; role: ReviewerRole };
export type AnchorPoint = { segment: number; offset: number };
export type Anchor = { start: AnchorPoint; end: AnchorPoint };
export type CommentMessage = { id: string; authorId: string; authorName: string; body: string; createdAt: Date };
export type CommentThread = {
  id: string;
  answerId: string;
  anchor: Anchor;
  createdBy: string;
  createdByName: string;
  createdAt: Date;
  resolvedAt: Date | null;
  resolvedByName: string | null;
  messages: CommentMessage[];
};

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Loads the question through the backend, which scopes it to this business's organisation. */
async function backendQuestion(userId: string, workspaceId: string, questionId: string) {
  if (!uuid.test(questionId)) throw notFound('Question');
  const client = await backendForWorkspace(userId, workspaceId);
  return { client, question: await client.get('/questions/{question_id}', { question_id: questionId }) };
}

type Member = { userId: string; displayName: string };
const activeMembers = (tx: Tx, workspaceId: string) =>
  tx<Member[]>`select m.user_id, p.display_name from app.memberships m join app.profiles p on p.user_id = m.user_id
               where m.workspace_id = ${workspaceId} and m.deactivated_at is null`;

async function notify(
  tx: Tx,
  workspaceId: string,
  actorId: string,
  recipients: Map<string, { event: string; summary: string }>,
  question: { id: string; tender_id: string },
  key: string,
) {
  for (const [recipient, { event, summary }] of recipients) {
    if (recipient === actorId) continue;
    await tx`select app.create_notification(${workspaceId}, ${recipient}, ${actorId}, ${event}, 'question', ${question.id},
                                            ${question.tender_id}, ${question.id}, ${summary}, ${key})`;
  }
}

// --- Reviewers -----------------------------------------------------------------------------

export async function listTenderReviewers(userId: string, workspaceId: string, tenderId: string): Promise<ReviewerRow[]> {
  if (!uuid.test(tenderId)) throw notFound('Tender');
  return inWorkspace(
    userId,
    workspaceId,
    (tx) => tx<ReviewerRow[]>`
      select r.question_id, r.user_id, p.display_name, r.role
      from app.question_reviewers r join app.profiles p on p.user_id = r.user_id
      join app.memberships m on m.workspace_id = r.workspace_id and m.user_id = r.user_id and m.deactivated_at is null
      where r.workspace_id = ${workspaceId} and r.tender_id = ${tenderId}
      order by r.assigned_at`,
  );
}

export async function listQuestionReviewers(userId: string, workspaceId: string, questionId: string): Promise<ReviewerRow[]> {
  if (!uuid.test(questionId)) throw notFound('Question');
  return inWorkspace(
    userId,
    workspaceId,
    (tx) => tx<ReviewerRow[]>`
      select r.question_id, r.user_id, p.display_name, r.role
      from app.question_reviewers r join app.profiles p on p.user_id = r.user_id
      join app.memberships m on m.workspace_id = r.workspace_id and m.user_id = r.user_id and m.deactivated_at is null
      where r.workspace_id = ${workspaceId} and r.question_id = ${questionId}
      order by r.assigned_at`,
  );
}


export async function addReviewer(userId: string, workspaceId: string, questionId: string, input: { userId: string; role: ReviewerRole }) {
  const { question } = await backendQuestion(userId, workspaceId, questionId);
  return inWorkspace(userId, workspaceId, async (tx, me) => {
    await assertActiveMembers(tx, workspaceId, [input.userId]);
    const inserted = await tx`
      insert into app.question_reviewers (workspace_id, tender_id, question_id, user_id, role, assigned_by)
      values (${workspaceId}, ${question.tender_id}, ${questionId}, ${input.userId}, ${input.role}, ${userId})
      on conflict do nothing returning user_id`;
    if (inserted.length) {
      const as = { sme: 'the SME reviewer', approver: 'the approver', reviewer: 'a reviewer' }[input.role];
      const summary = `${me.displayName} asked you to be ${as} for question ${question.number}.`;
      await notify(tx, workspaceId, userId, new Map([[input.userId, { event: 'review_requested', summary }]]), question, randomUUID());
    }
    return { added: inserted.length === 1 };
  });
}

export async function removeReviewer(userId: string, workspaceId: string, questionId: string, input: { userId: string; role: ReviewerRole }) {
  await backendQuestion(userId, workspaceId, questionId);
  return inWorkspace(userId, workspaceId, async (tx) => {
    const removed = await tx`
      delete from app.question_reviewers
      where workspace_id = ${workspaceId} and question_id = ${questionId} and user_id = ${input.userId} and role = ${input.role}
      returning user_id`;
    return { removed: removed.length === 1 };
  });
}

// --- Anchored comments ---------------------------------------------------------------------

export async function listCommentThreads(userId: string, workspaceId: string, questionId: string): Promise<CommentThread[]> {
  await backendQuestion(userId, workspaceId, questionId);
  return inWorkspace(userId, workspaceId, async (tx) => {
    const threads = await tx<Omit<CommentThread, 'messages'>[]>`
      select t.id, t.answer_id, t.anchor, t.created_by, p.display_name as created_by_name, t.created_at, t.resolved_at,
             r.display_name as resolved_by_name
      from app.comment_threads t
      join app.profiles p on p.user_id = t.created_by
      left join app.profiles r on r.user_id = t.resolved_by
      where t.workspace_id = ${workspaceId} and t.question_id = ${questionId}
      order by t.created_at`;
    const messages = threads.length
      ? await tx<(CommentMessage & { threadId: string })[]>`
          select c.id, c.thread_id, c.author_id, p.display_name as author_name, c.body, c.created_at
          from app.comment_messages c join app.profiles p on p.user_id = c.author_id
          where c.workspace_id = ${workspaceId} and c.thread_id in ${tx(threads.map((t) => t.id))}
          order by c.created_at`
      : [];
    return threads.map((t) => ({
      ...t,
      messages: messages.filter((m) => m.threadId === t.id).map(({ threadId: _, ...m }) => m),
    }));
  });
}

function validAnchor(anchor: Anchor, segments: { text: string }[]): boolean {
  const { start, end } = anchor;
  const inside = (p: AnchorPoint) =>
    Number.isInteger(p.segment) && Number.isInteger(p.offset) && p.segment >= 0 && p.segment < segments.length &&
    p.offset >= 0 && p.offset <= segments[p.segment].text.length;
  return inside(start) && inside(end) && (start.segment < end.segment || (start.segment === end.segment && start.offset < end.offset));
}

/** People to tell about a comment: the owner, reviewers, earlier participants and anyone @mentioned. */
async function commentRecipients(
  tx: Tx,
  workspaceId: string,
  question: { id: string; assignee?: string | null },
  body: string,
  participants: string[],
  actorName: string,
  reply: boolean,
) {
  const members = await activeMembers(tx, workspaceId);
  const reviewers = await tx<{ userId: string }[]>`
    select distinct user_id from app.question_reviewers where workspace_id = ${workspaceId} and question_id = ${question.id}`;
  const recipients = new Map<string, { event: string; summary: string }>();
  const general = { event: 'comment_added', summary: `${actorName} ${reply ? 'replied to a comment' : 'commented on an answer'}.` };
  for (const m of members) {
    if (m.displayName === question.assignee || reviewers.some((r) => r.userId === m.userId) || participants.includes(m.userId))
      recipients.set(m.userId, general);
  }
  const mentioned = mentionedNames(
    body,
    members.map((m) => m.displayName),
  );
  for (const m of members)
    if (mentioned.includes(m.displayName)) recipients.set(m.userId, { event: 'mentioned', summary: `${actorName} mentioned you in a comment.` });
  return recipients;
}

export async function createCommentThread(
  userId: string,
  workspaceId: string,
  questionId: string,
  input: { answerId: string; anchor: Anchor; body: string },
) {
  const { client, question } = await backendQuestion(userId, workspaceId, questionId);
  const versions = await client.get('/questions/{question_id}/answers', { question_id: questionId });
  const version = versions.find((v) => v.id === input.answerId);
  if (!version) throw notFound('Answer version');
  if (!validAnchor(input.anchor, version.segments ?? []))
    throw new AppError('VALIDATION_FAILED', 'Select text inside the answer before commenting.');
  return inWorkspace(userId, workspaceId, async (tx, me) => {
    const [thread] = await tx<{ id: string }[]>`
      insert into app.comment_threads (workspace_id, tender_id, question_id, answer_id, anchor, created_by)
      values (${workspaceId}, ${question.tender_id}, ${questionId}, ${input.answerId}, ${tx.json(input.anchor)}, ${userId})
      returning id`;
    const [message] = await tx<{ id: string }[]>`
      insert into app.comment_messages (thread_id, workspace_id, author_id, body)
      values (${thread.id}, ${workspaceId}, ${userId}, ${input.body}) returning id`;
    await notify(tx, workspaceId, userId, await commentRecipients(tx, workspaceId, question, input.body, [], me.displayName, false), question, message.id);
    return { id: thread.id };
  });
}

async function loadThread(tx: Tx, workspaceId: string, threadId: string) {
  if (!uuid.test(threadId)) throw notFound('Comment');
  const [thread] = await tx<{ id: string; questionId: string; tenderId: string }[]>`
    select id, question_id, tender_id from app.comment_threads where id = ${threadId} and workspace_id = ${workspaceId}`;
  if (!thread) throw notFound('Comment');
  return thread;
}

export async function replyToThread(userId: string, workspaceId: string, threadId: string, body: string) {
  const { questionId } = await inWorkspace(userId, workspaceId, (tx) => loadThread(tx, workspaceId, threadId));
  const { question } = await backendQuestion(userId, workspaceId, questionId);
  return inWorkspace(userId, workspaceId, async (tx, me) => {
    const [message] = await tx<{ id: string }[]>`
      insert into app.comment_messages (thread_id, workspace_id, author_id, body)
      values (${threadId}, ${workspaceId}, ${userId}, ${body}) returning id`;
    // Replying reopens a resolved thread, as in shared document editors.
    await tx`update app.comment_threads set resolved_at = null, resolved_by = null where id = ${threadId} and resolved_at is not null`;
    const participants = await tx<{ authorId: string }[]>`select distinct author_id from app.comment_messages where thread_id = ${threadId}`;
    const recipients = await commentRecipients(tx, workspaceId, question, body, participants.map((p) => p.authorId), me.displayName, true);
    await notify(tx, workspaceId, userId, recipients, question, message.id);
    return { id: message.id };
  });
}

export async function setThreadResolved(userId: string, workspaceId: string, threadId: string, resolved: boolean) {
  return inWorkspace(userId, workspaceId, async (tx) => {
    await loadThread(tx, workspaceId, threadId);
    await tx`update app.comment_threads
             set resolved_at = case when ${resolved} then now() end, resolved_by = ${resolved ? userId : null}
             where id = ${threadId}`;
    return { resolved };
  });
}

export async function deleteCommentMessage(userId: string, workspaceId: string, messageId: string) {
  if (!uuid.test(messageId)) throw notFound('Comment');
  return inWorkspace(userId, workspaceId, async (tx) => {
    const [row] = await tx<{ threadId: string }[]>`
      delete from app.comment_messages where id = ${messageId} and workspace_id = ${workspaceId} and author_id = ${userId}
      returning thread_id`;
    if (!row) throw new AppError('FORBIDDEN', 'You can delete only your own comments.');
    // A thread with no messages left disappears with its highlight.
    await tx`delete from app.comment_threads t where t.id = ${row.threadId}
             and not exists (select 1 from app.comment_messages c where c.thread_id = t.id)`;
    return { deleted: true };
  });
}
