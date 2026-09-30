// Operator-only mapping. Membership routes cannot update this column.
import { parseArgs } from 'node:util';
import { closeDb, withService } from '../src/server/db';
import { backendConfig } from '../src/server/backend/config';
import { backendFetch } from '../src/server/backend/transport';

async function main() {
  const { values } = parseArgs({ options: { workspace: { type: 'string' }, organisation: { type: 'string' } } });
  const { workspace, organisation } = values;
  if (![workspace, organisation].every((v) => v && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v))) {
    throw new Error('Usage: pnpm business:link-backend --workspace <uuid> --organisation <uuid>');
  }
  const response = await backendFetch(backendConfig(), { orgId: organisation!, actor: 'Workspace operator' }, '/tenders');
  await response.body?.cancel();
  if (!response.ok) throw new Error(`The backend refused this organisation (${response.status}). Nothing was linked.`);
  await withService(async (tx) => {
    const [row] = await tx<{ backendOrgId: string | null }[]>`select backend_org_id from app.workspaces where id = ${workspace!} for update`;
    if (!row) throw new Error('Workspace not found.');
    if (row.backendOrgId && row.backendOrgId !== organisation) throw new Error('This business already has a different organisation. Refusing to remap its content.');
    await tx`update app.workspaces set backend_org_id = ${organisation!} where id = ${workspace!}`;
  });
  console.log('Business linked to its backend organisation.');
}
main().catch((error) => { console.error((error as Error).message); process.exitCode = 1; }).finally(() => closeDb());
