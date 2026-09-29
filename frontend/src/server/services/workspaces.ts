import 'server-only';
import { devSwitchEnabled } from '@/lib/dev-accounts';
import { supabaseAdmin } from '../auth';
import { openQuestions } from '../backend/reviews';
import { taskCount } from './tasks';
import { withService, withUser, type Tx } from '../db';
import { env } from '../env';
import { AppError, invalid, notFound } from '../errors';
import { logActivity } from './activity';
import { inWorkspace, requireAdmin, type Role } from './context';

export type WorkspaceSummary = { id: string; name: string; role: Role };

export async function listMyWorkspaces(userId: string): Promise<WorkspaceSummary[]> {
  return withUser(
    userId,
    (tx) => tx<WorkspaceSummary[]>`
    select w.id, w.name, m.role
    from app.memberships m join app.workspaces w on w.id = m.workspace_id
    where m.user_id = ${userId} and m.deactivated_at is null
    order by w.name`,
  );
}

export type ShellData = {
  workspace: { id: string; name: string };
  me: { userId: string; displayName: string; email: string; role: Role };
  workspaces: WorkspaceSummary[];
  openReviewCount: number;
  unreadNotificationCount: number;
  /** Development stacks only: show the test-account switch in the account menu. */
  devSwitch: boolean;
};

export async function getShell(userId: string, workspaceId: string): Promise<ShellData> {
  const shell = await inWorkspace(userId, workspaceId, async (tx, me) => {
    const [ws] = await tx<{ id: string; name: string }[]>`select id, name from app.workspaces where id = ${workspaceId}`;
    const workspaces = await tx<WorkspaceSummary[]>`
      select w.id, w.name, m.role from app.memberships m join app.workspaces w on w.id = m.workspace_id
      where m.user_id = ${userId} and m.deactivated_at is null order by w.name`;
    const [counts] = await tx<{ reviews: number; unread: number }[]>`
      select
        0 as reviews,
        (select count(*)::int from app.notifications n
          where n.workspace_id = ${workspaceId} and n.recipient_id = ${userId} and n.read_at is null) as unread`;
    return {
      workspace: ws,
      me: { userId, displayName: me.displayName, email: me.email, role: me.role },
      workspaces,
      openReviewCount: counts.reviews,
      unreadNotificationCount: counts.unread,
      devSwitch: devSwitchEnabled(env().APP_MODE, process.env.DEMO_USER_SWITCH),
    };
  });
  shell.openReviewCount = await taskCount(userId, workspaceId);
  return shell;
}

export type MemberRow = {
  userId: string;
  displayName: string;
  email: string;
  role: Role;
  jobTitle: string | null;
  createdAt: Date;
  deactivatedAt: Date | null;
  openReviews: number;
};

export type InvitationRow = {
  id: string;
  email: string;
  role: Role;
  jobTitle: string | null;
  createdAt: Date;
  expiresAt: Date;
  invitedByName: string | null;
};

export async function getTeam(userId: string, workspaceId: string) {
  const team = await inWorkspace(userId, workspaceId, async (tx, me) => {
    const members = await tx<MemberRow[]>`
      select m.user_id, p.display_name, p.email, m.role, m.job_title, m.created_at, m.deactivated_at,
        0 as open_reviews
      from app.memberships m join app.profiles p on p.user_id = m.user_id
      where m.workspace_id = ${workspaceId}
      order by m.deactivated_at nulls first, p.display_name`;
    const invitations =
      me.role === 'admin'
        ? await tx<InvitationRow[]>`
            select i.id, i.email, i.role, i.job_title, i.created_at, i.expires_at, p.display_name as invited_by_name
            from app.invitations i left join app.profiles p on p.user_id = i.invited_by
            where i.workspace_id = ${workspaceId} and i.accepted_at is null and i.revoked_at is null
            order by i.created_at desc`
        : [];
    return { me, members, invitations };
  });
  const questions = await openQuestions(userId, workspaceId);
  team.members.forEach((member) => {
    member.openReviews = questions.filter((q) => q.assignee === member.displayName).length;
  });
  return team;
}

/** Active members, for owner/reviewer pickers and @mentions. */
export async function listActiveMembers(userId: string, workspaceId: string) {
  return inWorkspace(
    userId,
    workspaceId,
    (tx) => tx<{ userId: string; displayName: string; email: string; role: Role; jobTitle: string | null }[]>`
    select m.user_id, p.display_name, p.email, m.role, m.job_title
    from app.memberships m join app.profiles p on p.user_id = m.user_id
    where m.workspace_id = ${workspaceId} and m.deactivated_at is null
    order by p.display_name`,
  );
}

export function inviteLink(tokenHash: string, type: 'invite' | 'recovery'): string {
  const url = new URL('/auth/confirm', env().APP_URL);
  url.searchParams.set('token_hash', tokenHash);
  url.searchParams.set('type', type);
  return url.toString();
}

async function findProfileIdByEmail(email: string): Promise<string | null> {
  // Email lookup across workspaces needs the privileged connection; only the ID is used.
  const [row] = await withService((tx) => tx<{ userId: string }[]>`select user_id from app.profiles where lower(email) = ${email}`);
  return row?.userId ?? null;
}

export type InviteResult =
  | { kind: 'added_existing'; userId: string }
  | { kind: 'invited'; invitationId: string; link: string; expiresAt: Date };

/**
 * Adds a person to the workspace. Existing accounts are added straight away.
 * New people get a one-time invitation link that the admin shares with them;
 * no email is sent from the application.
 */
export async function inviteMember(
  userId: string,
  workspaceId: string,
  input: { email: string; role: Role; jobTitle?: string | null; displayName?: string | null },
): Promise<InviteResult> {
  const email = input.email.trim().toLowerCase();
  const existingId = await findProfileIdByEmail(email);

  if (existingId) {
    return inWorkspace(userId, workspaceId, async (tx, me) => {
      requireAdmin(me, 'add people');
      const [current] = await tx<{ deactivatedAt: Date | null }[]>`
        select deactivated_at from app.memberships where workspace_id = ${workspaceId} and user_id = ${existingId}`;
      if (current && !current.deactivatedAt) throw new AppError('CONFLICT', `${email} is already a member of this workspace.`);
      await tx`
        insert into app.memberships (workspace_id, user_id, role, job_title)
        values (${workspaceId}, ${existingId}, ${input.role}, ${input.jobTitle ?? null})
        on conflict (workspace_id, user_id) do update
          set role = excluded.role, job_title = excluded.job_title, deactivated_at = null, deactivated_by = null`;
      await logActivity(tx, {
        workspaceId,
        actorId: userId,
        type: 'member.added',
        objectType: 'membership',
        objectId: existingId,
        summary: `${me.displayName} added ${email} as ${input.role}.`,
      });
      return { kind: 'added_existing' as const, userId: existingId };
    });
  }

  // New person: confirm admin rights before touching Supabase Auth.
  await inWorkspace(userId, workspaceId, async (_tx, me) => requireAdmin(me, 'invite people'));
  const { data, error } = await supabaseAdmin().auth.admin.generateLink({
    type: 'invite',
    email,
    options: { data: { display_name: input.displayName?.trim() || email.split('@')[0] } },
  });
  if (error || !data?.properties?.hashed_token) {
    throw new AppError('INTERNAL', `Supabase Auth could not create the invitation: ${error?.message ?? 'no token returned'}`);
  }
  const authUserId = data.user?.id ?? null;
  return inWorkspace(userId, workspaceId, async (tx, me) => {
    const [inv] = await tx<{ id: string; expiresAt: Date }[]>`
      insert into app.invitations (workspace_id, email, role, job_title, invited_by, auth_user_id)
      values (${workspaceId}, ${email}, ${input.role}, ${input.jobTitle ?? null}, ${userId}, ${authUserId})
      on conflict (workspace_id, email) where accepted_at is null and revoked_at is null
        do update set role = excluded.role, job_title = excluded.job_title, auth_user_id = excluded.auth_user_id,
                      expires_at = now() + interval '7 days', invited_by = excluded.invited_by
      returning id, expires_at`;
    await logActivity(tx, {
      workspaceId,
      actorId: userId,
      type: 'member.invited',
      objectType: 'invitation',
      objectId: inv.id,
      summary: `${me.displayName} invited ${email} as ${input.role}.`,
    });
    return {
      kind: 'invited' as const,
      invitationId: inv.id,
      link: inviteLink(data.properties.hashed_token, 'invite'),
      expiresAt: inv.expiresAt,
    };
  });
}

/** New link for an invitation the person has not used yet. */
export async function regenerateInvitationLink(userId: string, workspaceId: string, invitationId: string) {
  const inv = await inWorkspace(userId, workspaceId, async (tx, me) => {
    requireAdmin(me, 'manage invitations');
    const [row] = await tx<{ id: string; email: string; authUserId: string | null }[]>`
      select id, email, auth_user_id from app.invitations
      where id = ${invitationId} and workspace_id = ${workspaceId} and accepted_at is null and revoked_at is null`;
    if (!row) throw notFound('Invitation');
    return row;
  });
  const [authUser] = await withService(
    (tx) => tx<{ lastSignInAt: Date | null }[]>`
    select last_sign_in_at from auth.users where lower(email) = ${inv.email}`,
  );
  if (authUser?.lastSignInAt) {
    throw new AppError('CONFLICT', 'This person has already signed in. Add them as an existing user instead.');
  }
  const { data, error } = await supabaseAdmin().auth.admin.generateLink({ type: authUser ? 'recovery' : 'invite', email: inv.email });
  if (error || !data?.properties?.hashed_token)
    throw new AppError('INTERNAL', `Supabase Auth could not create a link: ${error?.message ?? 'no token'}`);
  await withUser(userId, (tx) => tx`update app.invitations set expires_at = now() + interval '7 days' where id = ${invitationId}`);
  return { link: inviteLink(data.properties.hashed_token, authUser ? 'recovery' : 'invite') };
}

export async function revokeInvitation(userId: string, workspaceId: string, invitationId: string) {
  return inWorkspace(userId, workspaceId, async (tx, me) => {
    requireAdmin(me, 'manage invitations');
    const [row] = await tx<{ email: string }[]>`
      update app.invitations set revoked_at = now()
      where id = ${invitationId} and workspace_id = ${workspaceId} and accepted_at is null and revoked_at is null
      returning email`;
    if (!row) throw notFound('Invitation');
    await logActivity(tx, {
      workspaceId,
      actorId: userId,
      type: 'member.invitation_revoked',
      objectType: 'invitation',
      objectId: invitationId,
      summary: `${me.displayName} revoked the invitation for ${row.email}.`,
    });
    return { revoked: true };
  });
}

async function assertNotLastAdmin(tx: Tx, workspaceId: string, targetUserId: string) {
  const [row] = await tx<{ admins: number }[]>`
    select count(*)::int as admins from app.memberships
    where workspace_id = ${workspaceId} and role = 'admin' and deactivated_at is null and user_id <> ${targetUserId}`;
  if (row.admins === 0) throw new AppError('CONFLICT', 'A workspace needs at least one active admin.');
}

export async function changeMemberRole(userId: string, workspaceId: string, targetUserId: string, role: Role) {
  return inWorkspace(userId, workspaceId, async (tx, me) => {
    requireAdmin(me, 'change roles');
    if (role !== 'admin') await assertNotLastAdmin(tx, workspaceId, targetUserId);
    const [row] = await tx<{ displayName: string }[]>`
      update app.memberships m set role = ${role}
      from app.profiles p
      where m.workspace_id = ${workspaceId} and m.user_id = ${targetUserId} and m.deactivated_at is null and p.user_id = m.user_id
      returning p.display_name`;
    if (!row) throw notFound('Member');
    await logActivity(tx, {
      workspaceId,
      actorId: userId,
      type: 'member.role_changed',
      objectType: 'membership',
      objectId: targetUserId,
      summary: `${me.displayName} changed ${row.displayName}'s role to ${role}.`,
    });
    return { role };
  });
}

export async function updateMemberTitle(userId: string, workspaceId: string, targetUserId: string, jobTitle: string | null) {
  return inWorkspace(userId, workspaceId, async (tx, me) => {
    requireAdmin(me, 'edit member details');
    const rows =
      await tx`update app.memberships set job_title = ${jobTitle} where workspace_id = ${workspaceId} and user_id = ${targetUserId}`;
    if (rows.count === 0) throw notFound('Member');
    return { jobTitle };
  });
}

export async function deactivateMember(userId: string, workspaceId: string, targetUserId: string) {
  const questions = await openQuestions(userId, workspaceId);
  return inWorkspace(userId, workspaceId, async (tx, me) => {
    requireAdmin(me, 'remove people');
    await assertNotLastAdmin(tx, workspaceId, targetUserId);
    const [row] = await tx<{ displayName: string }[]>`
      update app.memberships m set deactivated_at = now(), deactivated_by = ${userId}
      from app.profiles p
      where m.workspace_id = ${workspaceId} and m.user_id = ${targetUserId} and m.deactivated_at is null and p.user_id = m.user_id
      returning p.display_name`;
    if (!row) throw notFound('Member');
    const open = { n: questions.filter((q) => q.assignee === row.displayName).length };
    await logActivity(tx, {
      workspaceId,
      actorId: userId,
      type: 'member.removed',
      objectType: 'membership',
      objectId: targetUserId,
      summary: `${me.displayName} removed ${row.displayName} from the workspace.`,
      details: { openReviewTasks: open.n },
    });
    return { removed: true, openReviewTasks: open.n };
  });
}

export async function updateMyProfile(userId: string, displayName: string) {
  const name = displayName.trim();
  if (name.length < 1 || name.length > 100) throw invalid('Enter a name between 1 and 100 characters.');
  await withUser(userId, (tx) => tx`update app.profiles set display_name = ${name}, updated_at = now() where user_id = ${userId}`);
  return { displayName: name };
}

export async function getMyProfile(userId: string) {
  const [row] = await withUser(
    userId,
    (tx) => tx<{ displayName: string; email: string }[]>`
    select display_name, email from app.profiles where user_id = ${userId}`,
  );
  return row ?? null;
}

/** Called after an invite or recovery link is verified. Uses the privileged path because the person is not a member yet. */
export async function acceptPendingInvitations(userId: string, email: string): Promise<number> {
  return withService(async (tx) => {
    const invites = await tx<{ id: string; workspaceId: string; role: Role; jobTitle: string | null }[]>`
      select id, workspace_id, role, job_title from app.invitations
      where email = ${email.toLowerCase()} and accepted_at is null and revoked_at is null and expires_at > now()
      for update`;
    for (const inv of invites) {
      await tx`
        insert into app.memberships (workspace_id, user_id, role, job_title)
        values (${inv.workspaceId}, ${userId}, ${inv.role}, ${inv.jobTitle})
        on conflict (workspace_id, user_id) do update set deactivated_at = null, role = excluded.role`;
      await tx`update app.invitations set accepted_at = now(), auth_user_id = ${userId} where id = ${inv.id}`;
      await logActivity(tx, {
        workspaceId: inv.workspaceId,
        actorId: userId,
        type: 'member.joined',
        objectType: 'membership',
        objectId: userId,
        summary: `${email} accepted their invitation and joined as ${inv.role}.`,
      });
    }
    return invites.length;
  });
}
