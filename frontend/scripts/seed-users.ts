// Creates the fictional development accounts and workspaces. Development and
// test environments only: these accounts have a published password.
import { provisionOrganisation } from '../src/server/backend/provision';
import { supabaseAdmin } from '../src/server/auth';
import { withService } from '../src/server/db';
import { env } from '../src/server/env';
import { DEV_ACCOUNTS, DEV_PASSWORD, type DevAccount } from '../src/lib/dev-accounts';

export { DEV_PASSWORD };
export const PEOPLE = Object.fromEntries(DEV_ACCOUNTS.map((a) => [a.key, a])) as Record<string, DevAccount>;

export type PersonKey = string;

export function assertNotDemo() {
  if (env().APP_MODE === 'demo') {
    throw new Error('Refusing to create published-password development accounts in the demo environment. Use `pnpm business:create` instead.');
  }
}

export async function ensureUser(key: PersonKey): Promise<string> {
  const person = PEOPLE[key];
  const [existing] = await withService((tx) => tx<{ id: string }[]>`select id from auth.users where lower(email) = ${person.email}`);
  if (existing) return existing.id;
  const { data, error } = await supabaseAdmin().auth.admin.createUser({
    email: person.email,
    password: DEV_PASSWORD,
    email_confirm: true,
    user_metadata: { display_name: person.name },
  });
  if (error || !data.user) throw new Error(`Could not create ${person.email}: ${error?.message}`);
  return data.user.id;
}

export async function ensureWorkspace(
  name: string,
  members: Array<{ userId: string; role: 'admin' | 'member'; title: string }>,
  options: { synthetic?: boolean } = {},
): Promise<string> {
  const workspaceId = await withService(async (tx) => {
    const [existing] = await tx<{ id: string }[]>`select id from app.workspaces where name = ${name}`;
    const id =
      existing?.id ??
      (await tx<{ id: string }[]>`insert into app.workspaces (name, created_by) values (${name}, ${members[0].userId}) returning id`)[0].id;
    for (const m of members) {
      await tx`
        insert into app.memberships (workspace_id, user_id, role, job_title)
        values (${id}, ${m.userId}, ${m.role}, ${m.title})
        on conflict (workspace_id, user_id) do update set role = excluded.role, job_title = excluded.job_title, deactivated_at = null`;
    }
    return id;
  });
  await provisionOrganisation(workspaceId, name, options);
  return workspaceId;
}

export async function seedPeopleAndWorkspaces() {
  assertNotDemo();
  const ids: Record<string, string> = {};
  for (const account of DEV_ACCOUNTS) ids[account.key] = await ensureUser(account.key);
  const members = (business: DevAccount['business']) =>
    DEV_ACCOUNTS.filter((a) => a.business === business).map((a) => ({ userId: ids[a.key], role: a.role, title: a.title }));
  // The two fictional businesses are synthetic demonstrations: simulated AI, synthetic uploads only.
  const exampleHealth = await ensureWorkspace('Example Health (fictional)', members('example'), { synthetic: true });
  const riverside = await ensureWorkspace('Riverside Medical (fictional, isolation checks)', members('riverside'), { synthetic: true });
  // The live business: the real providers from the repo-root .env, any document, and an empty library
  // of its own, so nothing synthetic is ever retrieved for a real tender. The Example Health people
  // are its members, so one sign-in reaches both through the business switcher.
  const live = await ensureWorkspace('Live workspace', members('example'), { synthetic: false });
  return { ids, exampleHealth, riverside, live };
}
