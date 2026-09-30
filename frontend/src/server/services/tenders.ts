import 'server-only';
import { backendConfig } from '../backend/config';
import { backendIdentity } from '../backend/context';
import { backendFetch } from '../backend/transport';
import { BackendError } from '../backend/client';
import { withService } from '../db';
import { notFound } from '../errors';
import { inWorkspace, requireAdmin } from './context';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Deletes an unsubmitted tender in the backend, then everything Ten kept about it. Admins only. */
export async function deleteTender(userId: string, workspaceId: string, tenderId: string) {
  if (!uuid.test(tenderId)) throw notFound('Tender');
  await inWorkspace(userId, workspaceId, async (_tx, member) => requireAdmin(member, 'delete tenders'));
  const response = await backendFetch(backendConfig(), await backendIdentity(userId, workspaceId), `/tenders/${tenderId}`, {
    method: 'DELETE',
  });
  if (response.status !== 204) throw new BackendError(response.status, await response.json().catch(() => ({})));
  // The backend has confirmed the tender belonged to this business, so remove its sidecar rows.
  await withService(async (tx) => {
    await tx`delete from app.question_reviewers where workspace_id = ${workspaceId} and tender_id = ${tenderId}`;
    await tx`delete from app.comment_threads where workspace_id = ${workspaceId} and tender_id = ${tenderId}`;
    await tx`delete from app.form_locations where workspace_id = ${workspaceId} and tender_id = ${tenderId}`;
    await tx`delete from app.notifications where workspace_id = ${workspaceId} and tender_id = ${tenderId}`;
  });
  return { deleted: true };
}

export async function memberRole(userId: string, workspaceId: string) {
  return inWorkspace(userId, workspaceId, async (_tx, member) => member.role);
}
