// Creates a business workspace and makes one person its first admin. Accounts
// are invite-only, so this is how a new environment (including the buyer demo)
// gets its first user. Prints a single-use link for a new person to set a
// password; an existing account is simply added.
//
// Usage: pnpm business:create --name "Example Health" --email person@example.com [--display-name "Name"] [--title "Job title"]
import { provisionOrganisation } from '../src/server/backend/provision';
import { parseArgs } from 'node:util';
import { supabaseAdmin } from '../src/server/auth';
import { closeDb, withService } from '../src/server/db';
import { inviteLink } from '../src/server/services/workspaces';

async function main() {
  const { values } = parseArgs({
    options: { name: { type: 'string' }, email: { type: 'string' }, 'display-name': { type: 'string' }, title: { type: 'string' } },
  });
  const name = values.name?.trim();
  const email = values.email?.trim().toLowerCase();
  if (!name || !email || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    throw new Error('Usage: pnpm business:create --name "<business name>" --email <first admin email> [--display-name "<name>"] [--title "<job title>"]');
  }

  const [existing] = await withService((tx) => tx<{ userId: string }[]>`select user_id from app.profiles where lower(email) = ${email}`);
  let userId = existing?.userId ?? null;
  let link: string | null = null;
  if (!userId) {
    const { data, error } = await supabaseAdmin().auth.admin.generateLink({
      type: 'invite',
      email,
      options: { data: { display_name: values['display-name']?.trim() || email.split('@')[0] } },
    });
    if (error || !data?.user || !data.properties?.hashed_token) throw new Error(`Supabase Auth could not create the invitation: ${error?.message ?? 'no token returned'}`);
    userId = data.user.id;
    link = inviteLink(data.properties.hashed_token, 'invite');
  }

  const workspaceId = await withService(async (tx) => {
    const [dupe] = await tx<{ id: string }[]>`select id from app.workspaces where name = ${name}`;
    if (dupe) throw new Error(`A workspace called "${name}" already exists (${dupe.id}).`);
    const [ws] = await tx<{ id: string }[]>`insert into app.workspaces (name, created_by) values (${name}, ${userId}) returning id`;
    await tx`insert into app.memberships (workspace_id, user_id, role, job_title) values (${ws.id}, ${userId}, 'admin', ${values.title?.trim() || null})`;
    return ws.id;
  });

  await provisionOrganisation(workspaceId, name);

  console.log(`Created "${name}" (${workspaceId}) with ${email} as admin.`);
  if (link) console.log(`Send this single-use link to ${email} so they can set a password (it expires):\n${link}`);
  else console.log(`${email} already has an account and can sign in now.`);
}

main()
  .catch((err) => {
    console.error(err instanceof Error ? err.message : err);
    process.exitCode = 1;
  })
  .finally(() => closeDb());
