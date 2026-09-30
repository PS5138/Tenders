'use client';
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api-client';
import type { Anchor } from '@/lib/comment-anchors';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ErrorNote } from './shared';
import { MentionText, MentionTextarea } from './mention-textarea';

type Member = { userId: string; displayName: string };

export type CommentMessage = { id: string; authorId: string; authorName: string; body: string; createdAt: string };
export type CommentThread = {
  id: string;
  answerId: string;
  anchor: Anchor;
  createdBy: string;
  createdByName: string;
  createdAt: string;
  resolvedAt: string | null;
  resolvedByName: string | null;
  messages: CommentMessage[];
};
/** A thread as shown against the current answer version. */
export type PlacedThread = CommentThread & { current: Anchor | null; quote: string | null; carried: boolean };

export function useCommentThreads(workspaceId: string, questionId: string) {
  const [threads, setThreads] = useState<CommentThread[]>([]),
    [error, setError] = useState<string | null>(null);
  const endpoint = `/api/w/${workspaceId}/questions/${questionId}/comments`;
  const reload = useCallback(async () => {
    try {
      setThreads(await api<CommentThread[]>(endpoint, { method: 'GET' }));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [endpoint]);
  useEffect(() => {
    let active = true;
    api<CommentThread[]>(endpoint, { method: 'GET' })
      .then((rows) => {
        if (active) setThreads(rows);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [endpoint]);
  return { threads, error, reload, endpoint };
}

const when = (value: string) =>
  new Date(value).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

function Quote({ text, outdated }: { text: string | null; outdated: boolean }) {
  if (!text) return <p className="text-xs italic text-muted">The commented text is no longer available.</p>;
  return (
    <p className={`line-clamp-2 border-l-2 pl-2 text-xs italic ${outdated ? 'border-line text-muted line-through' : 'border-amber text-ink'}`}>
      “{text}”
    </p>
  );
}

function ThreadCard({
  workspaceId,
  thread,
  me,
  members,
  active,
  onSelect,
  onChanged,
}: {
  workspaceId: string;
  thread: PlacedThread;
  me: string;
  members: Member[];
  active: boolean;
  onSelect: () => void;
  onChanged: () => void;
}) {
  const [reply, setReply] = useState(''),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<string | null>(null);
  const outdated = !thread.current;
  async function send(body: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/w/${workspaceId}/comments/${thread.id}`, { body });
      if (body.action === 'reply') setReply('');
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <article
      id={`comment-thread-${thread.id}`}
      aria-label={`Comment by ${thread.createdByName}`}
      onClick={onSelect}
      className={`space-y-2 rounded-lg border p-3 text-sm ${active ? 'border-amber shadow-sm' : 'border-line'} ${thread.resolvedAt ? 'bg-soft' : 'bg-bg'}`}
    >
      <Quote text={thread.quote} outdated={outdated} />
      {outdated && !thread.resolvedAt ? <Badge tone="amber">The text changed since this comment</Badge> : null}
      {thread.carried && !outdated ? <p className="text-[11px] text-muted">Made on an earlier version of this answer.</p> : null}
      {thread.messages.map((m) => (
        <div key={m.id}>
          <p className="flex items-baseline justify-between gap-2 text-xs">
            <span className="font-semibold">{m.authorName}</span>
            <span className="text-muted">{when(m.createdAt)}</span>
          </p>
          <MentionText text={m.body} members={members} />
          {m.authorId === me ? (
            <button
              type="button"
              className="text-[11px] text-muted underline hover:text-red"
              disabled={busy}
              onClick={(e) => {
                e.stopPropagation();
                if (window.confirm('Delete this comment?')) void send({ action: 'delete_message', messageId: m.id });
              }}
            >
              Delete
            </button>
          ) : null}
        </div>
      ))}
      {thread.resolvedAt ? (
        <p className="text-[11px] text-muted">
          Resolved by {thread.resolvedByName ?? 'a colleague'} · {when(thread.resolvedAt)}
        </p>
      ) : null}
      <form
        className="space-y-2"
        onSubmit={(e) => {
          e.preventDefault();
          void send({ action: 'reply', body: reply });
        }}
      >
        <MentionTextarea
          members={members}
          className="min-h-9 text-[13px]"
          aria-label="Reply"
          placeholder="Reply. Type @ to mention someone."
          value={reply}
          onValue={setReply}
          rows={reply ? 3 : 1}
        />
        <div className="flex flex-wrap gap-2">
          {reply.trim() ? (
            <Button size="sm" type="submit" variant="primary" busy={busy}>
              Reply
            </Button>
          ) : null}
          <Button size="sm" busy={busy} onClick={() => void send({ action: thread.resolvedAt ? 'reopen' : 'resolve' })}>
            {thread.resolvedAt ? 'Reopen' : 'Resolve'}
          </Button>
        </div>
      </form>
      <ErrorNote error={error} />
    </article>
  );
}

export function CommentsPanel({
  workspaceId,
  me,
  members,
  threads,
  error,
  endpoint,
  answerId,
  draft,
  onDraftDone,
  activeThread,
  onSelect,
  onChanged,
}: {
  workspaceId: string;
  me: string;
  members: Member[];
  threads: PlacedThread[];
  error: string | null;
  endpoint: string;
  answerId: string | null;
  draft: { anchor: Anchor; quote: string } | null;
  onDraftDone: () => void;
  activeThread: string | null;
  onSelect: (id: string) => void;
  onChanged: () => void;
}) {
  const [body, setBody] = useState(''),
    [busy, setBusy] = useState(false),
    [draftError, setDraftError] = useState<string | null>(null);
  const open = threads.filter((t) => !t.resolvedAt);
  const resolved = threads.filter((t) => t.resolvedAt);
  const position = (t: PlacedThread) => (t.current ? t.current.start.segment * 1e6 + t.current.start.offset : Number.MAX_SAFE_INTEGER);
  return (
    <div className="space-y-3">
      <ErrorNote error={error} />
      {draft && answerId ? (
        <form
          className="space-y-2 rounded-lg border border-accent p-3"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setDraftError(null);
            try {
              const created = await api<{ id: string }>(endpoint, { body: { answerId, anchor: draft.anchor, body } });
              setBody('');
              onDraftDone();
              onChanged();
              onSelect(created.id);
            } catch (e) {
              setDraftError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <Quote text={draft.quote} outdated={false} />
          <MentionTextarea
            members={members}
            className="min-h-20 text-[13px]"
            aria-label="New comment"
            placeholder="Add a comment. Type @ to mention someone."
            autoFocus
            required
            value={body}
            onValue={setBody}
          />
          <ErrorNote error={draftError} />
          <div className="flex gap-2">
            <Button size="sm" type="submit" variant="primary" busy={busy}>
              Comment
            </Button>
            <Button size="sm" variant="ghost" onClick={onDraftDone}>
              Cancel
            </Button>
          </div>
        </form>
      ) : null}
      {!open.length && !draft ? (
        <p className="rounded-lg border border-dashed border-line p-4 text-center text-xs text-muted">
          Select words in the answer, then choose Comment, to discuss a specific part of it.
        </p>
      ) : null}
      {[...open]
        .sort((a, b) => position(a) - position(b))
        .map((t) => (
          <ThreadCard
            key={t.id}
            workspaceId={workspaceId}
            thread={t}
            me={me}
            members={members}
            active={activeThread === t.id}
            onSelect={() => onSelect(t.id)}
            onChanged={onChanged}
          />
        ))}
      {resolved.length ? (
        <details>
          <summary className="cursor-pointer text-xs font-semibold text-muted">Resolved ({resolved.length})</summary>
          <div className="mt-2 space-y-3">
            {resolved.map((t) => (
              <ThreadCard
                key={t.id}
                workspaceId={workspaceId}
                thread={t}
                me={me}
                members={members}
                active={activeThread === t.id}
                onSelect={() => onSelect(t.id)}
                onChanged={onChanged}
              />
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}
