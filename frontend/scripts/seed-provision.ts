// Development accounts, the two fictional businesses and their backend organisations
// (POST /organisations). Sign-up is disabled, so this must finish before `web` serves a
// login page; it is the `frontend-provision` service in docker-compose.yml. Idempotent.
// Library documents and the sample tender pack are seeded separately by seed-backend.ts,
// which `web` does not wait for.
import { closeDb } from '../src/server/db';
import { seedPeopleAndWorkspaces } from './seed-users';

async function main() {
  const { exampleHealth, riverside, live } = await seedPeopleAndWorkspaces();
  console.log(`Development accounts ready. Example Health business: ${exampleHealth}. Riverside Medical business: ${riverside}.`);
  console.log(`Live workspace (real AI, your own documents): ${live}.`);
  console.log('Each business has its own backend organisation and library.');
}
main()
  .catch((error) => {
    console.error((error as Error).message);
    process.exitCode = 1;
  })
  .finally(() => closeDb());
