import 'server-only';
import { inWorkspace } from '../services/context';
import { AppError } from '../errors';
import { validActor } from './policy';

export type BackendIdentity = { actor: string; orgId: string };

/** Resolve identity inside RLS, then release the DB transaction before an upload or stream. */
export async function backendIdentity(userId: string, workspaceId: string): Promise<BackendIdentity> {
  return inWorkspace(userId, workspaceId, async (tx, member) => {
    const [workspace] = await tx<{ backendOrgId: string | null }[]>`
      select backend_org_id from app.workspaces where id = ${workspaceId}`;
    if (!workspace?.backendOrgId) throw new AppError('AI_UNAVAILABLE', 'This business has not been linked to its backend organisation.');
    if (!validActor(member.displayName))
      throw new AppError('VALIDATION_FAILED', 'Choose a non-empty display name other than “system” before using the backend.');
    const [names] = await tx<{ count: number }[]>`
      select count(*)::int as count from app.memberships m join app.profiles p on p.user_id = m.user_id
      where m.workspace_id = ${workspaceId} and m.deactivated_at is null
        and lower(trim(p.display_name)) = lower(${member.displayName})`;
    if (names.count !== 1)
      throw new AppError('CONFLICT', 'Display names must be unique within this business. Update your account name before continuing.');
    return { actor: member.displayName, orgId: workspace.backendOrgId };
  });
}
