import 'server-only';
import { withUser, type Tx } from '../db';
import { AppError, notFound } from '../errors';

export type Role = 'admin' | 'member';

export type Member = {
  userId: string;
  workspaceId: string;
  role: Role;
  displayName: string;
  email: string;
};

export async function loadMember(tx: Tx, userId: string, workspaceId: string): Promise<Member | null> {
  const [row] = await tx<Member[]>`
    select m.user_id, m.workspace_id, m.role, p.display_name, p.email
    from app.memberships m
    join app.profiles p on p.user_id = m.user_id
    where m.workspace_id = ${workspaceId} and m.user_id = ${userId} and m.deactivated_at is null`;
  return row ?? null;
}

/**
 * Opens a transaction as `userId` (row-level security applies) and confirms the
 * user is an active member of the workspace before running `fn`.
 */
export async function inWorkspace<T>(
  userId: string,
  workspaceId: string,
  fn: (tx: Tx, member: Member) => Promise<T>,
): Promise<T> {
  if (!/^[0-9a-f-]{36}$/i.test(workspaceId)) throw notFound('Workspace');
  return withUser(userId, async (tx) => {
    const member = await loadMember(tx, userId, workspaceId);
    if (!member) throw notFound('Workspace');
    return fn(tx, member);
  });
}

export function requireAdmin(member: Member, action = 'do that'): void {
  if (member.role !== 'admin') throw new AppError('FORBIDDEN', `Only workspace admins can ${action}.`);
}

/** Confirms that every user ID is an active member of the workspace. */
export async function assertActiveMembers(tx: Tx, workspaceId: string, userIds: string[]): Promise<void> {
  const unique = [...new Set(userIds)];
  if (unique.length === 0) return;
  const rows = await tx<{ userId: string }[]>`
    select user_id from app.memberships
    where workspace_id = ${workspaceId} and user_id in ${tx(unique)} and deactivated_at is null`;
  if (rows.length !== unique.length) {
    throw new AppError('VALIDATION_FAILED', 'One or more people are not active members of this workspace.');
  }
}
