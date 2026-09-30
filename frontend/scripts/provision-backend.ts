// Retry a partially completed business bootstrap without creating another business.
import { parseArgs } from 'node:util';
import { closeDb, withService } from '../src/server/db';
import { provisionOrganisation } from '../src/server/backend/provision';
async function main() {
  const { values } = parseArgs({ options: { workspace: { type: 'string' } } });
  if (!values.workspace || !/^([0-9a-f]{8}-)([0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(values.workspace))
    throw new Error('Usage: pnpm business:provision-backend --workspace <business UUID>');
  const [workspace] = await withService((tx) => tx<{ name: string }[]>`select name from app.workspaces where id=${values.workspace!}`);
  if (!workspace) throw new Error('Business not found.');
  await provisionOrganisation(values.workspace, workspace.name);
  console.log('Backend organisation provisioned and linked.');
}
main()
  .catch((error) => {
    console.error((error as Error).message);
    process.exitCode = 1;
  })
  .finally(() => closeDb());
