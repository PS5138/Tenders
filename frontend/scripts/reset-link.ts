// Operator tool: prints a single-use password reset link for an existing account.
// Needs server credentials, so it is not exposed in the app, where an admin of
// one business could otherwise take over a person who also belongs to another.
// Usage: pnpm user:reset-link --email person@example.com
import { parseArgs } from 'node:util';
import { supabaseAdmin } from '../src/server/auth';
import { closeDb, withService } from '../src/server/db';
import { inviteLink } from '../src/server/services/workspaces';

async function main() {
  const { values } = parseArgs({ options: { email: { type: 'string' } } });
  const email = values.email?.trim().toLowerCase();
  if (!email) throw new Error('Usage: pnpm user:reset-link --email <address>');
  const [user] = await withService((tx) => tx<{ id: string }[]>`select id from auth.users where lower(email) = ${email}`);
  if (!user) throw new Error(`No account exists for ${email}. Invite them from the Team page or with pnpm business:create.`);
  const { data, error } = await supabaseAdmin().auth.admin.generateLink({ type: 'recovery', email });
  if (error || !data?.properties?.hashed_token) throw new Error(`Supabase Auth could not create the link: ${error?.message ?? 'no token returned'}`);
  console.log(`Single-use password reset link for ${email} (it expires):\n${inviteLink(data.properties.hashed_token, 'recovery')}`);
}

main()
  .catch((err) => {
    console.error(err instanceof Error ? err.message : err);
    process.exitCode = 1;
  })
  .finally(() => closeDb());
