import 'server-only';
import { randomUUID } from 'node:crypto';
import { inWorkspace } from '../services/context';
import { AppError } from '../errors';
import { createBackendClient } from './client';
import type { BackendIdentity } from './context';
import type { components } from './schema';
import { mentionedNames } from '@/lib/mentions';
type Question = components['schemas']['QuestionDetail'];
type Member = { userId: string; displayName: string };
export async function prepareQuestionMutation(
  userId: string,
  workspaceId: string,
  identity: BackendIdentity,
  questionId: string,
  body: Record<string, unknown>,
) {
  const members = await inWorkspace(
    userId,
    workspaceId,
    (tx) =>
      tx<
        Member[]
      >`select m.user_id,p.display_name from app.memberships m join app.profiles p on p.user_id=m.user_id where m.workspace_id=${workspaceId} and m.deactivated_at is null`,
  );
  if (body.assignee != null && !members.some((m) => m.displayName === body.assignee))
    throw new AppError('VALIDATION_FAILED', 'Choose an active member of this business.');
  const before = await createBackendClient(identity).get('/questions/{question_id}', { question_id: questionId });
  return { before, members };
}
export async function notifyQuestionMutation(
  userId: string,
  workspaceId: string,
  identity: BackendIdentity,
  prepared: { before: Question; members: Member[] },
  body: Record<string, unknown>,
  isComment: boolean,
) {
  const { before, members } = prepared;
  const recipients = new Map<string, string>();
  const mentioned = (text: string) =>
    mentionedNames(
      text,
      members.map((m) => m.displayName),
    );
  for (const member of members) {
    if (member.userId === userId) continue;
    if (body.assignee === member.displayName && body.assignee !== before.assignee) recipients.set(member.userId, 'assigned');
    if (body.status && body.status !== before.status && member.displayName === (body.assignee ?? before.assignee))
      recipients.set(member.userId, 'status_changed');
    if (isComment && member.displayName === before.assignee) recipients.set(member.userId, 'comment_added');
    if (isComment && typeof body.text === 'string' && mentioned(body.text).includes(member.displayName)) recipients.set(member.userId, 'mentioned');
  }
  const operation = randomUUID();
  await inWorkspace(userId, workspaceId, async (tx) => {
    for (const [recipient, event] of recipients) {
      // Only notification metadata is kept here, never question or answer content.
      const summary = `${identity.actor} ${event === 'assigned' ? 'assigned a question to you' : event === 'status_changed' ? 'changed a question’s review status' : event === 'mentioned' ? 'mentioned you in a note' : 'commented on a question for you'}.`;
      await tx`select app.create_notification(${workspaceId},${recipient},${userId},${event},'question',${before.id},${before.tender_id},${before.id},${summary},${operation})`;
    }
  });
}
