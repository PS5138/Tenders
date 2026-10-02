import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The backend is the record for anchored comments: these tests pin the order of writes (backend
// first, sidecar only after success), the text the backend receives, and that hidden messages are
// filtered rather than deleted. The sidecar is a fake tagged-template `tx` that logs its SQL.

type Call = { sql: string; values: unknown[] };
type Rows = (sql: string, values: unknown[]) => unknown[] | undefined;

const mocks = vi.hoisted(() => ({
  fetch: vi.fn(),
  inWorkspace: vi.fn(),
  rows: { current: (() => undefined) as Rows },
  calls: [] as Call[],
  log: [] as string[],
}));

function fakeTx() {
  const tx = (strings: TemplateStringsArray | unknown[], ...values: unknown[]) => {
    if (!('raw' in strings)) return { list: strings }; // `in ${tx(ids)}`
    const sql = strings.join('$').replace(/\s+/g, ' ').trim();
    mocks.calls.push({ sql, values });
    mocks.log.push(`sql ${sql.split(' ').slice(0, 3).join(' ')}`);
    return Promise.resolve(mocks.rows.current(sql, values) ?? []);
  };
  return Object.assign(tx, { json: (value: unknown) => ({ json: value }) });
}

vi.mock('@/server/services/context', () => ({
  inWorkspace: mocks.inWorkspace,
  assertActiveMembers: vi.fn(),
}));
vi.mock('@/server/backend/context', () => ({
  backendIdentity: vi.fn(async () => ({ actor: 'Roger', orgId: 'org-1' })),
}));
vi.mock('@/server/backend/config', () => ({
  backendConfig: () => ({ origin: 'http://api:8000' }),
}));

import {
  backendCommentText,
  createCommentThread,
  deleteCommentMessage,
  listCommentThreads,
  replyToThread,
  truncateQuote,
} from '@/server/services/collaboration';
import { BackendError } from '@/server/backend/client';
import { AppError } from '@/server/errors';

const workspace = '11111111-1111-4111-8111-111111111111';
const questionId = '22222222-2222-4222-8222-222222222222';
const answerId = '33333333-3333-4333-8333-333333333333';
const threadId = '44444444-4444-4444-8444-444444444444';
const backendCommentId = '55555555-5555-4555-8555-555555555555';
const me = 'user-roger';
const anchor = { start: { segment: 1, offset: 0 }, end: { segment: 1, offset: 33 } };
const segments = [
  { index: 0, text: 'We release monthly.' },
  { index: 1, text: 'Our named clinical safety officer signs off every release.' },
];

const backendPosts = () => mocks.fetch.mock.calls.filter(([, init]) => (init as RequestInit).method === 'POST');
const postedText = (call: unknown[]) => (JSON.parse((call[1] as RequestInit).body as string) as { text: string }).text;

beforeEach(() => {
  mocks.calls.length = 0;
  mocks.log.length = 0;
  mocks.rows.current = () => undefined;
  mocks.fetch.mockReset();
  mocks.inWorkspace.mockReset();
  mocks.inWorkspace.mockImplementation(async (_user: string, _workspace: string, fn: (tx: unknown, member: unknown) => Promise<unknown>) =>
    fn(fakeTx(), { userId: me, workspaceId: workspace, role: 'member', displayName: 'Roger', email: 'roger@example.test' }),
  );
  vi.stubGlobal('fetch', mocks.fetch);
  mocks.fetch.mockImplementation(async (url: URL, init: RequestInit) => {
    const path = url.pathname;
    if (init.method === 'POST' && path === `/questions/${questionId}/comments`) {
      mocks.log.push('backend POST comments');
      const { text } = JSON.parse(init.body as string) as { text: string };
      return Response.json({ id: backendCommentId, author: 'Roger', text, created_at: '2026-09-30T09:00:00Z' }, { status: 201 });
    }
    if (path === `/questions/${questionId}/answers`) return Response.json([{ id: answerId, question_id: questionId, version: 1, segments }]);
    if (path === `/questions/${questionId}`) return Response.json({ id: questionId, tender_id: 'tender-1', number: '2.1', assignee: null });
    return Response.json({ detail: 'Not found.' }, { status: 404 });
  });
});
afterEach(() => vi.unstubAllGlobals());

describe('the text written to the backend record', () => {
  it('quotes the anchored words, then the comment, under a stable thread reference', () => {
    expect(backendCommentText(threadId, 'named clinical safety officer', '  Can we name them?  ')).toBe(
      `[thread ${threadId}] “named clinical safety officer”: Can we name them?`,
    );
    expect(backendCommentText(threadId, null, 'Agreed.')).toBe(`[thread ${threadId}] Agreed.`);
    expect(backendCommentText(threadId, '   ', 'Agreed.')).toBe(`[thread ${threadId}] Agreed.`);
  });

  it('truncates a long quote at a word and collapses whitespace', () => {
    const words = Array.from({ length: 40 }, (_, i) => `word${i}`).join(' ');
    const cut = truncateQuote(words);
    expect(cut.length).toBeLessThanOrEqual(121);
    expect(cut.endsWith('…')).toBe(true);
    expect(cut.slice(0, -1)).toMatch(/word\d+$/);
    expect(words.startsWith(cut.slice(0, -1))).toBe(true);
    expect(truncateQuote('two\n  lines   here')).toBe('two lines here');
    expect(truncateQuote('x'.repeat(200))).toBe(`${'x'.repeat(120)}…`);
  });
});

describe('creating a thread', () => {
  it('writes to the backend first, then the sidecar row carrying the backend comment id', async () => {
    mocks.rows.current = (sql, values) => {
      if (sql.startsWith('insert into app.comment_threads')) return [{ id: values[0] }];
      if (sql.startsWith('insert into app.comment_messages')) return [{ id: 'message-1' }];
      return [];
    };
    const result = await createCommentThread(me, workspace, questionId, { answerId, anchor, body: 'Can we name them? @Sam' });

    expect(backendPosts()).toHaveLength(1);
    const [url, init] = backendPosts()[0] as [URL, RequestInit];
    expect(url.pathname).toBe(`/questions/${questionId}/comments`);
    expect(new Headers(init.headers).get('x-actor')).toBe('Roger');
    expect(new Headers(init.headers).get('content-type')).toBe('application/json');
    expect(postedText(backendPosts()[0])).toBe(`[thread ${result.id}] “Our named clinical safety officer”: Can we name them? @Sam`);

    const firstInsert = mocks.log.findIndex((entry) => entry.startsWith('sql insert'));
    expect(firstInsert).toBeGreaterThan(mocks.log.indexOf('backend POST comments'));
    const thread = mocks.calls.find((c) => c.sql.startsWith('insert into app.comment_threads'))!;
    expect(thread.values[0]).toBe(result.id);
    const message = mocks.calls.find((c) => c.sql.startsWith('insert into app.comment_messages'))!;
    expect(message.sql).toContain('backend_comment_id');
    expect(message.values).toEqual([result.id, workspace, me, 'Can we name them? @Sam', backendCommentId]);
  });

  it('writes nothing to the sidecar and returns the backend error when the backend refuses', async () => {
    mocks.fetch.mockImplementation(async (url: URL, init: RequestInit) => {
      if (init.method === 'POST') return Response.json({ detail: 'Question not found.' }, { status: 404 });
      if (url.pathname === `/questions/${questionId}/answers`) return Response.json([{ id: answerId, segments }]);
      return Response.json({ id: questionId, tender_id: 'tender-1', number: '2.1' });
    });
    await expect(createCommentThread(me, workspace, questionId, { answerId, anchor, body: 'Hello' })).rejects.toMatchObject({
      status: 404,
      message: 'Question not found.',
    });
    await expect(createCommentThread(me, workspace, questionId, { answerId, anchor, body: 'Hello' })).rejects.toBeInstanceOf(BackendError);
    expect(mocks.inWorkspace).not.toHaveBeenCalled();
    expect(mocks.calls).toEqual([]);
  });

  it('reports an unreachable backend without touching the sidecar', async () => {
    mocks.fetch.mockImplementation(async (url: URL, init: RequestInit) => {
      if (init.method === 'POST') throw new TypeError('fetch failed');
      if (url.pathname === `/questions/${questionId}/answers`) return Response.json([{ id: answerId, segments }]);
      return Response.json({ id: questionId, tender_id: 'tender-1', number: '2.1' });
    });
    await expect(createCommentThread(me, workspace, questionId, { answerId, anchor, body: 'Hello' })).rejects.toMatchObject({
      code: 'AI_UNAVAILABLE',
    });
    expect(mocks.inWorkspace).not.toHaveBeenCalled();
  });

  it('still validates the anchor before anything is written anywhere', async () => {
    await expect(
      createCommentThread(me, workspace, questionId, { answerId, anchor: { start: anchor.start, end: anchor.start }, body: 'Empty' }),
    ).rejects.toBeInstanceOf(AppError);
    expect(backendPosts()).toHaveLength(0);
    expect(mocks.inWorkspace).not.toHaveBeenCalled();
  });
});

describe('replying', () => {
  it('records the reply in the backend under the same thread reference before the sidecar insert', async () => {
    mocks.rows.current = (sql) => {
      if (sql.startsWith('select id, question_id, tender_id, answer_id, anchor'))
        return [{ id: threadId, questionId, tenderId: 'tender-1', answerId, anchor }];
      if (sql.startsWith('insert into app.comment_messages')) return [{ id: 'message-2' }];
      if (sql.startsWith('select distinct author_id')) return [{ authorId: me }];
      return [];
    };
    const result = await replyToThread(me, workspace, threadId, 'Yes, it is Dr Patel.');
    expect(result).toEqual({ id: 'message-2' });
    expect(backendPosts()).toHaveLength(1);
    expect(postedText(backendPosts()[0])).toBe(`[thread ${threadId}] “Our named clinical safety officer”: Yes, it is Dr Patel.`);
    const insertAt = mocks.log.findIndex((entry) => entry.startsWith('sql insert'));
    expect(insertAt).toBeGreaterThan(mocks.log.indexOf('backend POST comments'));
    const message = mocks.calls.find((c) => c.sql.startsWith('insert into app.comment_messages'))!;
    expect(message.values).toEqual([threadId, workspace, me, 'Yes, it is Dr Patel.', backendCommentId]);
  });

  it('adds no sidecar message when the backend write fails', async () => {
    mocks.rows.current = (sql) =>
      sql.startsWith('select id, question_id') ? [{ id: threadId, questionId, tenderId: 'tender-1', answerId, anchor }] : [];
    mocks.fetch.mockImplementation(async (url: URL, init: RequestInit) => {
      if (init.method === 'POST') return Response.json({ detail: 'Service unavailable.' }, { status: 503 });
      if (url.pathname === `/questions/${questionId}/answers`) return Response.json([{ id: answerId, segments }]);
      return Response.json({ id: questionId, tender_id: 'tender-1', number: '2.1' });
    });
    await expect(replyToThread(me, workspace, threadId, 'Lost reply')).rejects.toMatchObject({ status: 503 });
    expect(mocks.calls.filter((c) => /^(insert|update|delete)/.test(c.sql))).toEqual([]);
  });
});

describe('reading and hiding', () => {
  it('filters hidden messages and drops threads with nothing left to show', async () => {
    const other = '66666666-6666-4666-8666-666666666666';
    mocks.rows.current = (sql) => {
      if (sql.startsWith('select t.id, t.answer_id'))
        return [
          { id: threadId, answerId, anchor, createdBy: me, createdByName: 'Roger', createdAt: new Date(), resolvedAt: null, resolvedByName: null },
          { id: other, answerId, anchor, createdBy: me, createdByName: 'Roger', createdAt: new Date(), resolvedAt: null, resolvedByName: null },
        ];
      if (sql.startsWith('select c.id, c.thread_id')) {
        expect(sql).toContain('c.hidden_at is null');
        return [{ id: 'message-1', threadId, authorId: me, authorName: 'Roger', body: 'Visible', createdAt: new Date() }];
      }
      return [];
    };
    const threads = await listCommentThreads(me, workspace, questionId);
    expect(threads.map((t) => t.id)).toEqual([threadId]);
    expect(threads[0].messages.map((m) => m.body)).toEqual(['Visible']);
  });

  it('hides a message instead of deleting it, and only the author may', async () => {
    mocks.rows.current = (sql, values) => (sql.startsWith('update app.comment_messages set hidden_at') && values[2] === me ? [{ threadId }] : []);
    expect(await deleteCommentMessage(me, workspace, 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')).toEqual({ deleted: true });
    expect(mocks.calls.some((c) => c.sql.startsWith('delete'))).toBe(false);
    expect(mocks.calls[0].sql).toContain('set hidden_at = coalesce(hidden_at, now())');
    expect(mocks.calls[0].sql).toContain('author_id = $');
    await expect(deleteCommentMessage('someone-else', workspace, 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')).rejects.toMatchObject({ code: 'FORBIDDEN' });
    expect(backendPosts()).toHaveLength(0);
  });
});
