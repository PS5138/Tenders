import 'server-only';
import { backendConfig } from './config';
import { backendFetch } from './transport';
import { withService } from '../db';
export async function provisionOrganisation(workspaceId: string, name: string) {
  const response = await backendFetch(backendConfig(), { actor: 'Workspace operator', orgId: workspaceId }, '/organisations', {
    method: 'POST',
    contentType: 'application/json',
    body: JSON.stringify({ id: workspaceId, name }),
  });
  if (!response.ok)
    throw new Error(
      `Organisation provisioning failed (${response.status}). Retry provisioning business ${workspaceId} using business:provision-backend.`,
    );
  await withService(async (tx) => {
    const rows =
      await tx`update app.workspaces set backend_org_id=${workspaceId} where id=${workspaceId} and (backend_org_id is null or backend_org_id=${workspaceId}) returning id`;
    if (rows.length !== 1) throw new Error('Business mapping already differs or the business does not exist.');
  });
}
