-- Reviewers per question and comments anchored to a span of an answer.
--
-- Both refer to backend records by UUID only. Neither stores question or answer
-- text: a comment's anchor is a segment index and offsets into one answer
-- version, and the quoted words are read back from the backend when shown.

create table app.question_reviewers (
  workspace_id uuid not null references app.workspaces (id) on delete cascade,
  tender_id uuid not null,
  question_id uuid not null,
  user_id uuid not null,
  role text not null check (role in ('sme', 'approver', 'reviewer')),
  assigned_by uuid references app.profiles (user_id),
  assigned_at timestamptz not null default now(),
  primary key (workspace_id, question_id, user_id, role),
  foreign key (workspace_id, user_id) references app.memberships (workspace_id, user_id) on delete cascade
);
create index question_reviewers_tender_idx on app.question_reviewers (workspace_id, tender_id);
create index question_reviewers_user_idx on app.question_reviewers (workspace_id, user_id);

-- An anchor is {start: {segment, offset}, end: {segment, offset}}: segment indices of the
-- answer version and UTF-16 offsets into each segment's text, end exclusive.
create table app.comment_threads (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references app.workspaces (id) on delete cascade,
  tender_id uuid not null,
  question_id uuid not null,
  answer_id uuid not null,
  anchor jsonb not null check (
    jsonb_typeof(anchor -> 'start' -> 'segment') = 'number' and jsonb_typeof(anchor -> 'start' -> 'offset') = 'number'
    and jsonb_typeof(anchor -> 'end' -> 'segment') = 'number' and jsonb_typeof(anchor -> 'end' -> 'offset') = 'number'
  ),
  created_by uuid not null references app.profiles (user_id),
  created_at timestamptz not null default now(),
  resolved_at timestamptz,
  resolved_by uuid references app.profiles (user_id)
);
create index comment_threads_question_idx on app.comment_threads (workspace_id, question_id, created_at);

create table app.comment_messages (
  id uuid primary key default gen_random_uuid(),
  thread_id uuid not null references app.comment_threads (id) on delete cascade,
  workspace_id uuid not null references app.workspaces (id) on delete cascade,
  author_id uuid not null references app.profiles (user_id),
  body text not null check (length(trim(body)) between 1 and 4000),
  created_at timestamptz not null default now()
);
create index comment_messages_thread_idx on app.comment_messages (thread_id, created_at);

grant select, insert, update, delete on app.question_reviewers, app.comment_threads, app.comment_messages to authenticated;
grant all on app.question_reviewers, app.comment_threads, app.comment_messages to service_role;

-- Members of the business see and assign reviewers.
alter table app.question_reviewers enable row level security;
create policy question_reviewers_read on app.question_reviewers for select to authenticated using (app.is_member(workspace_id));
create policy question_reviewers_write on app.question_reviewers for insert to authenticated
  with check (app.is_member(workspace_id) and assigned_by = auth.uid());
create policy question_reviewers_delete on app.question_reviewers for delete to authenticated using (app.is_member(workspace_id));

-- Members read every thread; people start threads and write messages only as themselves.
alter table app.comment_threads enable row level security;
create policy comment_threads_read on app.comment_threads for select to authenticated using (app.is_member(workspace_id));
create policy comment_threads_insert on app.comment_threads for insert to authenticated
  with check (app.is_member(workspace_id) and created_by = auth.uid());
-- Resolving and reopening is open to any member; the anchor and author never change.
create policy comment_threads_update on app.comment_threads for update to authenticated
  using (app.is_member(workspace_id)) with check (app.is_member(workspace_id));
-- A thread goes only once its last message has been deleted.
create policy comment_threads_delete on app.comment_threads for delete to authenticated
  using (app.is_member(workspace_id) and not exists (select 1 from app.comment_messages c where c.thread_id = comment_threads.id));
revoke update on app.comment_threads from authenticated;
grant update (resolved_at, resolved_by) on app.comment_threads to authenticated;

alter table app.comment_messages enable row level security;
create policy comment_messages_read on app.comment_messages for select to authenticated using (app.is_member(workspace_id));
create policy comment_messages_insert on app.comment_messages for insert to authenticated
  with check (
    app.is_member(workspace_id) and author_id = auth.uid()
    and exists (select 1 from app.comment_threads t where t.id = thread_id and t.workspace_id = comment_messages.workspace_id)
  );
create policy comment_messages_delete on app.comment_messages for delete to authenticated using (author_id = auth.uid());
