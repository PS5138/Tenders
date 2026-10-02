-- Anchored comments are mirrored into the backend record. Every message is first written through
-- POST /questions/{id}/comments, which stores the backend comments row and a comment_added event
-- with the actor; the sidecar row keeps the anchor and the backend comment id. The backend record
-- persists, so messages are never hard-deleted here: an author can hide their own message from the
-- Comments tab, and reads filter hidden messages out.

alter table app.comment_messages
  add column backend_comment_id uuid,
  add column hidden_at timestamptz;

comment on column app.comment_messages.backend_comment_id is
  'The backend comments row this message was written to first; null only for rows made before the mirror existed.';
comment on column app.comment_messages.hidden_at is
  'Set when the author hides the message from the Comments tab. The backend comment remains the record.';

-- Hiding replaces deletion. Authors update only hidden_at, and only on their own messages.
drop policy comment_messages_delete on app.comment_messages;
revoke delete on app.comment_messages from authenticated;
revoke update on app.comment_messages from authenticated;
grant update (hidden_at) on app.comment_messages to authenticated;
create policy comment_messages_hide on app.comment_messages for update to authenticated
  using (author_id = auth.uid()) with check (author_id = auth.uid());

-- Threads keep their messages, so ordinary requests no longer delete threads either. Tender
-- deletion still removes a tender's threads through the service role.
drop policy comment_threads_delete on app.comment_threads;
revoke delete on app.comment_threads from authenticated;
