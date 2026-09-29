import 'server-only';
import { openQuestions } from '../backend/reviews';
import { backendIdentity } from '../backend/context';
import { inWorkspace } from './context';
import { mentionedNames } from '@/lib/mentions';
import { taskFor, type Assigned } from '@/lib/tasks';
import type { ReviewerRole } from '@/lib/review-roles';

type OpenQuestion = Awaited<ReturnType<typeof openQuestions>>[number];
export type TaskItem = {
  question: OpenQuestion;
  turns: ReturnType<typeof taskFor>;
};
export type ReplyItem = {
  threadId: string;
  question: OpenQuestion;
  lastAuthor: string;
  lastBody: string;
  lastAt: Date;
  mentioned: boolean;
  messageCount: number;
};
type ThreadRow = {
  id: string;
  questionId: string;
  messages: { authorId: string; authorName: string; body: string; createdAt: string }[];
};

/** Deadline first (undated last), then heaviest weighting, then the buyer's order. */
function byUrgency(a: OpenQuestion, b: OpenQuestion) {
  const da = a.tenderDeadline ? Date.parse(a.tenderDeadline) : Infinity;
  const db = b.tenderDeadline ? Date.parse(b.tenderDeadline) : Infinity;
  return da - db || (b.weighting ?? 0) - (a.weighting ?? 0) || a.order_index - b.order_index;
}

/** Everything this person should act on: questions whose turn it is, replies owed, and what is coming. */
export async function myTasks(userId: string, workspaceId: string) {
  const [identity, questions] = await Promise.all([backendIdentity(userId, workspaceId), openQuestions(userId, workspaceId)]);
  const byId = new Map(questions.map((q) => [q.id, q]));
  const { reviewers, threads } = await inWorkspace(userId, workspaceId, async (tx) => ({
    reviewers: await tx<{ questionId: string; userId: string; displayName: string; role: ReviewerRole }[]>`
      select r.question_id, r.user_id, p.display_name, r.role
      from app.question_reviewers r
      join app.profiles p on p.user_id = r.user_id
      join app.memberships m on m.workspace_id = r.workspace_id and m.user_id = r.user_id and m.deactivated_at is null
      where r.workspace_id = ${workspaceId}`,
    threads: await tx<ThreadRow[]>`
      select t.id, t.question_id,
        coalesce((select json_agg(json_build_object('authorId', c.author_id, 'authorName', p.display_name, 'body', c.body, 'createdAt', c.created_at)
                                  order by c.created_at)
                  from app.comment_messages c join app.profiles p on p.user_id = c.author_id
                  where c.thread_id = t.id), '[]'::json) as messages
      from app.comment_threads t
      where t.workspace_id = ${workspaceId} and t.resolved_at is null`,
  }));

  const yourTurn: TaskItem[] = [];
  const comingUp: TaskItem[] = [];
  const involved = new Set<string>();
  for (const q of questions) {
    const on = reviewers.filter((r) => r.questionId === q.id);
    const roles = on.filter((r) => r.userId === userId).map((r) => r.role);
    const owner = q.assignee === identity.actor;
    if (!owner && !roles.length) continue;
    involved.add(q.id);
    const assigned: Assigned = {
      sme: on.filter((r) => r.role === 'sme').map((r) => r.displayName),
      approver: on.filter((r) => r.role === 'approver').map((r) => r.displayName),
      reviewer: on.filter((r) => r.role === 'reviewer').map((r) => r.displayName),
    };
    const turns = taskFor(q, { owner, roles }, assigned);
    (turns.some((t) => t.turn) ? yourTurn : comingUp).push({ question: q, turns });
  }

  const replies: ReplyItem[] = [];
  for (const t of threads) {
    const question = byId.get(t.questionId);
    const last = t.messages.at(-1);
    if (!question || !last || last.authorId === userId) continue;
    const mentioned = t.messages.some((m) => mentionedNames(m.body, [identity.actor]).length > 0);
    const joined = t.messages.some((m) => m.authorId === userId);
    if (!mentioned && !joined && !involved.has(t.questionId)) continue;
    replies.push({
      threadId: t.id,
      question,
      lastAuthor: last.authorName,
      lastBody: last.body,
      lastAt: new Date(last.createdAt),
      mentioned,
      messageCount: t.messages.length,
    });
  }

  yourTurn.sort((a, b) => byUrgency(a.question, b.question));
  comingUp.sort((a, b) => byUrgency(a.question, b.question));
  replies.sort((a, b) => b.lastAt.getTime() - a.lastAt.getTime());
  return { yourTurn, replies, comingUp };
}

/** The sidebar count: questions waiting on this person plus replies they owe. */
export async function taskCount(userId: string, workspaceId: string) {
  const { yourTurn, replies } = await myTasks(userId, workspaceId);
  return yourTurn.length + replies.length;
}
