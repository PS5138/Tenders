import 'server-only';
import { backendConfig } from './config';
import { backendFetch } from './transport';
import { withService } from '../db';
/**
 * Creates (or confirms) the business's backend organisation. `synthetic: true` marks a demonstration
 * business: the backend runs its AI on the synthetic stand-ins and accepts only the supplied synthetic
 * files there, whatever the deployment's providers are. Omitted, a new organisation is live and an
 * existing one keeps its flag.
 */
export async function provisionOrganisation(workspaceId: string, name: string, options: { synthetic?: boolean } = {}) {
  const response = await backendFetch(backendConfig(), { actor: 'Workspace operator', orgId: workspaceId }, '/organisations', {
    method: 'POST',
    contentType: 'application/json',
    body: JSON.stringify({ id: workspaceId, name, ...(options.synthetic === undefined ? {} : { synthetic: options.synthetic }) }),
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
