-- Ten identity and sidecar schema. Backend content is referenced by UUID only.
--
-- All application tables live in the `app` schema, which is not exposed through
-- the Supabase Data API. The application server connects directly to Postgres,
-- switches to the `authenticated` role and sets `request.jwt.claims` for each
-- request, so the row-level security policies below apply to every user request.

create schema if not exists app;
create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- People and workspaces
-- ---------------------------------------------------------------------------

create table app.profiles (
  user_id uuid primary key references auth.users (id) on delete cascade,
  email text not null,
  display_name text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index profiles_email_key on app.profiles (lower(email));

create or replace function app.sync_profile_from_auth()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into app.profiles (user_id, email, display_name)
  values (
    new.id,
    coalesce(new.email, ''),
    coalesce(nullif(new.raw_user_meta_data ->> 'display_name', ''), split_part(coalesce(new.email, ''), '@', 1))
  )
  on conflict (user_id) do update
    set email = excluded.email,
        updated_at = now();
  return new;
end;
$$;

create trigger on_auth_user_saved
  after insert or update of email on auth.users
  for each row execute function app.sync_profile_from_auth();

create table app.workspaces (
  id uuid primary key default gen_random_uuid(),
  name text not null check (length(trim(name)) between 1 and 200),
  backend_org_id uuid unique,
  created_by uuid references app.profiles (user_id),
  created_at timestamptz not null default now()
);

create table app.memberships (
  workspace_id uuid not null references app.workspaces (id) on delete cascade,
  user_id uuid not null references app.profiles (user_id) on delete cascade,
  role text not null check (role in ('admin', 'member')),
  job_title text,
  created_at timestamptz not null default now(),
  deactivated_at timestamptz,
  deactivated_by uuid references app.profiles (user_id),
  primary key (workspace_id, user_id)
);
create index memberships_user_idx on app.memberships (user_id) where deactivated_at is null;

create table app.invitations (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references app.workspaces (id) on delete cascade,
  email text not null check (email = lower(email) and position('@' in email) > 1),
  role text not null check (role in ('admin', 'member')),
  job_title text,
  invited_by uuid references app.profiles (user_id),
  auth_user_id uuid references auth.users (id) on delete set null,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null default now() + interval '7 days',
  accepted_at timestamptz,
  revoked_at timestamptz
);
create unique index invitations_open_email_key on app.invitations (workspace_id, email)
  where accepted_at is null and revoked_at is null;

create table app.activity_events (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references app.workspaces (id) on delete cascade,
  tender_id uuid,
  actor_id uuid,
  type text not null,
  object_type text not null,
  object_id uuid,
  object_version text,
  summary text not null,
  details jsonb not null default '{}'::jsonb,
  occurred_at timestamptz not null default now(),
  foreign key (workspace_id, actor_id) references app.memberships (workspace_id, user_id)
);
create index activity_tender_idx on app.activity_events (workspace_id, tender_id, occurred_at desc);

create table app.notifications (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references app.workspaces (id) on delete cascade,
  recipient_id uuid not null,
  actor_id uuid,
  event_type text not null check (event_type in (
    'review_requested', 'mentioned', 'changes_requested', 'review_completed',
    'review_superseded', 'review_reassigned', 'job_failed', 'recheck_required', 'draft_ready', 'assigned', 'status_changed', 'comment_added'
  )),
  object_type text not null,
  object_id uuid not null,
  tender_id uuid,
  response_id uuid,
  summary text not null,
  idempotency_key text,
  read_at timestamptz,
  created_at timestamptz not null default now(),
  foreign key (workspace_id, recipient_id) references app.memberships (workspace_id, user_id),
  foreign key (workspace_id, actor_id) references app.memberships (workspace_id, user_id)
);
create index notifications_recipient_idx on app.notifications (recipient_id, workspace_id, created_at desc);
create unique index notifications_idempotency_key on app.notifications (workspace_id, recipient_id, idempotency_key)
  where idempotency_key is not null;

create table app.form_locations (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references app.workspaces(id) on delete cascade,
  tender_id uuid not null,
  question_id uuid not null,
  document_id uuid not null,
  target jsonb not null,
  confirmed_by uuid not null references app.profiles(user_id),
  confirmed_at timestamptz not null default now(),
  unique(workspace_id, question_id, document_id)
);
-- Row-level security. Every user request runs as the `authenticated` role with
-- the caller's JWT claims, so these policies bound what any request can read or
-- change, independently of the application's own checks.

create or replace function app.is_member(ws uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from app.memberships m
    where m.workspace_id = ws and m.user_id = auth.uid() and m.deactivated_at is null
  );
$$;

create or replace function app.is_admin(ws uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from app.memberships m
    where m.workspace_id = ws and m.user_id = auth.uid() and m.deactivated_at is null and m.role = 'admin'
  );
$$;

create or replace function app.shares_workspace_with(other_user uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from app.memberships mine
    join app.memberships theirs on theirs.workspace_id = mine.workspace_id
    where mine.user_id = auth.uid() and mine.deactivated_at is null and theirs.user_id = other_user
  );
$$;

revoke all on schema app from public, anon;
grant usage on schema app to authenticated, service_role;
grant select, insert, update, delete on all tables in schema app to authenticated;
grant all on all tables in schema app to service_role;
grant execute on function app.is_member(uuid), app.is_admin(uuid), app.shares_workspace_with(uuid) to authenticated;
alter default privileges in schema app grant select, insert, update, delete on tables to authenticated;
alter default privileges in schema app grant all on tables to service_role;

-- Profiles: yourself and people you share a workspace with.
alter table app.profiles enable row level security;
create policy profiles_select on app.profiles for select to authenticated
  using (user_id = auth.uid() or app.shares_workspace_with(user_id));
create policy profiles_update on app.profiles for update to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

-- Workspaces: members read; admins rename. Creation happens through a privileged path.
alter table app.workspaces enable row level security;
create policy workspaces_select on app.workspaces for select to authenticated using (app.is_member(id));
create policy workspaces_update on app.workspaces for update to authenticated
  using (app.is_admin(id)) with check (app.is_admin(id));

-- Memberships: members see their colleagues; only admins change membership or roles.
alter table app.memberships enable row level security;
create policy memberships_select on app.memberships for select to authenticated using (app.is_member(workspace_id));
create policy memberships_insert on app.memberships for insert to authenticated with check (app.is_admin(workspace_id));
create policy memberships_update on app.memberships for update to authenticated
  using (app.is_admin(workspace_id)) with check (app.is_admin(workspace_id));
create policy memberships_delete on app.memberships for delete to authenticated using (app.is_admin(workspace_id));

-- Invitations: admins only.
alter table app.invitations enable row level security;
create policy invitations_admin on app.invitations for all to authenticated
  using (app.is_admin(workspace_id)) with check (app.is_admin(workspace_id));

-- Notifications: members can create them for colleagues; only the recipient reads or marks them.
alter table app.notifications enable row level security;
create policy notifications_select on app.notifications for select to authenticated
  using (recipient_id = auth.uid() and app.is_member(workspace_id));
create policy notifications_insert on app.notifications for insert to authenticated with check (app.is_member(workspace_id));
create policy notifications_update on app.notifications for update to authenticated
  using (recipient_id = auth.uid() and app.is_member(workspace_id))
  with check (recipient_id = auth.uid() and app.is_member(workspace_id));

-- Members create notifications for colleagues, but can only read their own.
-- INSERT ... ON CONFLICT would also require SELECT visibility of the new row,
-- so idempotent creation goes through this function, which checks membership
-- of both the caller and the recipient itself.
create or replace function app.create_notification(
  p_workspace_id uuid,
  p_recipient_id uuid,
  p_actor_id uuid,
  p_event_type text,
  p_object_type text,
  p_object_id uuid,
  p_tender_id uuid,
  p_response_id uuid,
  p_summary text,
  p_idempotency_key text
) returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  caller uuid := auth.uid();
begin
  if caller is not null then
    if not app.is_member(p_workspace_id) then
      raise exception 'not a member of this workspace' using errcode = '42501';
    end if;
    if p_actor_id is distinct from caller then
      raise exception 'notifications must be created by their actor' using errcode = '42501';
    end if;
  end if;
  if not exists (
    select 1 from app.memberships m
    where m.workspace_id = p_workspace_id and m.user_id = p_recipient_id and m.deactivated_at is null
  ) then
    return; -- Former members are not notified.
  end if;
  insert into app.notifications (workspace_id, recipient_id, actor_id, event_type, object_type, object_id, tender_id, response_id, summary, idempotency_key)
  values (p_workspace_id, p_recipient_id, p_actor_id, p_event_type, p_object_type, p_object_id, p_tender_id, p_response_id, p_summary, p_idempotency_key)
  on conflict (workspace_id, recipient_id, idempotency_key) where idempotency_key is not null do nothing;
end;
$$;

revoke all on function app.create_notification(uuid, uuid, uuid, text, text, uuid, uuid, uuid, text, text) from public;
grant execute on function app.create_notification(uuid, uuid, uuid, text, text, uuid, uuid, uuid, text, text) to authenticated, service_role;

-- Direct inserts by ordinary requests are no longer needed.
drop policy notifications_insert on app.notifications;

revoke update on app.workspaces from authenticated;
grant update (name) on app.workspaces to authenticated;
alter table app.activity_events enable row level security;
create policy activity_read on app.activity_events for select to authenticated using (app.is_member(workspace_id));
create policy activity_write on app.activity_events for insert to authenticated with check (app.is_member(workspace_id) and actor_id = auth.uid());
alter table app.form_locations enable row level security;
create policy form_members on app.form_locations for all to authenticated using (app.is_member(workspace_id)) with check (app.is_member(workspace_id));

-- One small identity lock serialises name/membership changes across businesses.
create function app.check_actor_names() returns trigger language plpgsql security definer set search_path = '' as $$
declare actor_name text;
begin
  perform pg_advisory_xact_lock(62926001);
  if tg_table_name = 'profiles' then
    if new.display_name <> trim(new.display_name) or length(new.display_name) not between 1 and 100
       or lower(new.display_name) = 'system' or new.display_name ~ '[[:cntrl:]]' then
      raise exception 'Choose a valid display name other than system.' using errcode = '23514';
    end if;
    if exists (select 1 from app.memberships mine join app.memberships colleague on colleague.workspace_id = mine.workspace_id
      join app.profiles p on p.user_id = colleague.user_id
      where mine.user_id = new.user_id and mine.deactivated_at is null and colleague.deactivated_at is null
        and colleague.user_id <> new.user_id and lower(trim(p.display_name)) = lower(new.display_name)) then
      raise exception 'Display names must be unique within a business.' using errcode = '23505';
    end if;
  elsif new.deactivated_at is null then
    select display_name into actor_name from app.profiles where user_id = new.user_id;
    if exists (select 1 from app.memberships m join app.profiles p on p.user_id=m.user_id
      where m.workspace_id=new.workspace_id and m.deactivated_at is null and m.user_id<>new.user_id
        and lower(trim(p.display_name))=lower(trim(actor_name))) then
      raise exception 'Display names must be unique within a business.' using errcode = '23505';
    end if;
  end if;
  return new;
end;
$$;
create trigger profiles_actor_name before insert or update of display_name on app.profiles for each row execute function app.check_actor_names();
create trigger memberships_actor_name before insert or update on app.memberships for each row execute function app.check_actor_names();
