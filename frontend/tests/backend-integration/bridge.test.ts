/** Real PostgreSQL RLS + real FastAPI transport. The session boundary is stubbed;
 * browser cookie verification remains the separate Playwright acceptance gate.
 * Run ONLY against a disposable backend with fake providers and a running worker.
 */
import { randomUUID } from 'node:crypto';
import { readFile, readdir, writeFile } from 'node:fs/promises';
import { openAsBlob } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import postgres from 'postgres';
import { afterAll, beforeAll, expect, it, vi } from 'vitest';

const auth = vi.hoisted(() => ({ user: '' }));
vi.mock('@/server/auth', () => ({
  requireUser: async () => ({ id: auth.user, email: 'test@synthetic.test' }),
}));

if (!process.env.BRIDGE_TEST_DATABASE_URL || !process.env.BACKEND_URL || process.env.BRIDGE_TEST_ALLOW_SYNTHETIC_WRITES !== '1') {
  throw new Error(
    'Set BRIDGE_TEST_DATABASE_URL to a disposable backend DB, BACKEND_URL to its fake-provider API, and BRIDGE_TEST_ALLOW_SYNTHETIC_WRITES=1. A worker must be running.',
  );
}
const database = `ten_bridge_${randomUUID().replaceAll('-', '')}`;
const backendAdmin = postgres(process.env.BRIDGE_TEST_DATABASE_URL, {
  max: 1,
  onnotice: () => {},
});
const admin = postgres(process.env.BRIDGE_TEST_FRONTEND_DATABASE_URL ?? process.env.BRIDGE_TEST_DATABASE_URL, {
  max: 1,
  onnotice: () => {},
});
const frontendUrl = new URL(process.env.BRIDGE_TEST_FRONTEND_DATABASE_URL ?? process.env.BRIDGE_TEST_DATABASE_URL);
frontendUrl.pathname = `/${database}`;
Object.assign(process.env, {
  APP_MODE: 'test',
  APP_URL: 'http://localhost:3000',
  DATABASE_URL: frontendUrl.toString(),
  SUPABASE_URL: 'http://127.0.0.1:9999',
  SUPABASE_ANON_KEY: 'synthetic-test-placeholder-key',
  SUPABASE_SERVICE_ROLE_KEY: 'synthetic-test-placeholder-key',
});

const userA = randomUUID(),
  userB = randomUUID();
const workspaceA = randomUUID(),
  workspaceB = randomUUID();
const orgA = randomUUID(),
  orgB = randomUUID();
let sql: ReturnType<typeof postgres>;
let proxyBackend: (typeof import('@/server/backend/proxy'))['proxyBackend'];
let closeDb: (typeof import('@/server/db'))['closeDb'];
let withUser: (typeof import('@/server/db'))['withUser'];
let tenderB: string, documentB: string, questionB: string;

async function proxy(
  resource: string,
  method = 'GET',
  body?: BodyInit,
  contentType?: string,
  workspace = workspaceA,
  extraHeaders: Record<string, string> = {},
) {
  const request = new Request(`http://localhost:3000/api/w/${workspace}/backend${resource}`, {
    method,
    body,
    headers: {
      origin: 'http://localhost:3000',
      'x-ten-request': '1',
      ...(contentType ? { 'content-type': contentType } : {}),
      ...extraHeaders,
    },
  });
  return proxyBackend(request, workspace, resource.split('?')[0].slice(1).split('/'));
}

beforeAll(async () => {
  const healthResponse = await fetch(`${process.env.BACKEND_URL}/health`, {
    headers: process.env.BACKEND_SERVICE_SECRET ? { authorization: `Bearer ${process.env.BACKEND_SERVICE_SECRET}` } : {},
  });
  expect(healthResponse.ok).toBe(true);
  const health = await healthResponse.json();
  expect(health).toMatchObject({ llm_provider: 'fake', embedding_provider: 'fake', synthetic_demo: true });
  await admin.unsafe(`create database ${database}`);
  sql = postgres(frontendUrl.toString(), { max: 1, onnotice: () => {} });
  await sql.unsafe(await readFile('scripts/local/bootstrap.sql', 'utf8'));
  // Minimal Auth schema for membership tests. No claim about GoTrue/browser validation.
  await sql.unsafe(`create table auth.users (id uuid primary key, email text, raw_user_meta_data jsonb);
    create function auth.uid() returns uuid language sql stable as $$ select (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')::uuid $$;`);
  for (const file of (await readdir('supabase/migrations')).filter((f) => f.endsWith('.sql')).sort()) {
    await sql.unsafe(await readFile(path.join('supabase/migrations', file), 'utf8'));
  }
  for (const [user, name, workspace, org] of [
    [userA, 'Writer A', workspaceA, orgA],
    [userB, 'Writer B', workspaceB, orgB],
  ]) {
    await sql`insert into auth.users values (${user}, ${`${user}@synthetic.test`}, ${sql.json({ display_name: name })})`;
    await sql`insert into app.workspaces (id, name, backend_org_id) values (${workspace}, ${name}, ${org})`;
    await sql`insert into app.memberships (workspace_id, user_id, role) values (${workspace}, ${user}, 'admin')`;
    // Test-only fixture setup, never an application provisioning path.
    await backendAdmin`insert into organisations (id, org_id, name, topic_taxonomy, created_at, updated_at)
      select ${org}, ${org}, ${`Bridge test ${name}`}, topic_taxonomy, now(), now() from organisations where id = '00000000-0000-4000-8000-000000000001'`;
  }
  ({ proxyBackend } = await import('@/server/backend/proxy'));
  ({ closeDb, withUser } = await import('@/server/db'));
  auth.user = userB;
  const tenderResponse = await proxy(
    '/tenders',
    'POST',
    JSON.stringify({ name: 'Synthetic bridge isolation pack' }),
    'application/json',
    workspaceB,
  );
  expect(tenderResponse.status).toBe(201);
  tenderB = (await tenderResponse.json()).id;
  const form = new FormData();
  const filename = 'question_pack_northern_fells_2025.xlsx';
  form.append('file', await openAsBlob(path.resolve('../eval/data', filename)), filename);
  form.append('tender_doc_kind', 'question_pack');
  const upload = await proxy(`/tenders/${tenderB}/documents`, 'POST', form, undefined, workspaceB);
  expect(upload.status).toBe(202);
  const document = await upload.json();
  documentB = document.id;
  const deadline = Date.now() + 100_000;
  while (Date.now() < deadline) {
    const jobResponse = await proxy(`/jobs/${document.job_id}`, 'GET', undefined, undefined, workspaceB);
    const job = await jobResponse.json();
    if (job.status === 'failed') throw new Error(job.error);
    if (job.status === 'done') break;
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  const questions = await (await proxy(`/tenders/${tenderB}/questions`, 'GET', undefined, undefined, workspaceB)).json();
  expect(questions.length).toBeGreaterThan(0);
  questionB = questions[0].id;
  auth.user = userA;
});

afterAll(async () => {
  if (closeDb) await closeDb();
  if (sql) await sql.end();
  await admin.unsafe(`drop database if exists ${database}`);
  await admin.end();
  await backendAdmin.end();
});

it('returns 404 across businesses for tenders, questions, documents and the other workspace', async () => {
  for (const resource of [`/tenders/${tenderB}`, `/questions/${questionB}`, `/documents/${documentB}`]) {
    expect((await proxy(resource)).status).toBe(404);
  }
  expect((await proxy('/tenders', 'GET', undefined, undefined, workspaceB)).status).toBe(404);
  expect(await (await proxy('/tenders')).json()).toEqual([]);
});

it('prevents even a business admin from changing the backend organisation mapping', async () => {
  await expect(
    withUser(userA, async (tx) => {
      await tx`update app.workspaces set backend_org_id = ${orgB} where id = ${workspaceA}`;
    }),
  ).rejects.toMatchObject({ code: '42501' });
});

it('refuses an identity spoof at the authenticated route boundary', async () => {
  expect(
    (
      await proxy('/tenders', 'GET', undefined, undefined, workspaceA, {
        'x-actor': 'Writer B',
      })
    ).status,
  ).toBe(400);
  expect(
    (
      await proxy('/tenders', 'GET', undefined, undefined, workspaceA, {
        'x-org-id': orgB,
      })
    ).status,
  ).toBe(400);
});

it('streams the real fixture incrementally and retains the terminal fail_after error', async () => {
  const { consumeDraftStream } = await import('@/lib/backend-stream');
  for (const fail of [false, true]) {
    const response = await proxy(`/fixtures/draft?delay_ms=40${fail ? '&fail_after=3' : ''}`, 'POST', '{}', 'application/json');
    expect(response.status).toBe(200);
    const times: number[] = [],
      events: string[] = [];
    await consumeDraftStream(response.body!, (event) => {
      events.push(event.type);
      times.push(Date.now());
    });
    expect(events.at(-1)).toBe(fail ? 'error' : 'done');
    expect(times.at(-1)! - times[0]).toBeGreaterThan(80);
    if (fail) expect(events.filter((type) => type === 'segment')).toHaveLength(3);
    else expect(events).toContain('support');
  }
});

it('enforces sidecar RLS for reads, writes and notifications', async () => {
  expect(await withUser(userA, (tx) => tx`select * from app.memberships where workspace_id=${workspaceB}`)).toEqual([]);
  await expect(
    withUser(
      userA,
      (tx) =>
        tx`insert into app.form_locations(workspace_id,tender_id,question_id,document_id,target,confirmed_by) values(${workspaceB},${tenderB},${questionB},${documentB},'{}',${userA})`,
    ),
  ).rejects.toMatchObject({ code: '42501' });
  await expect(
    withUser(
      userA,
      (tx) =>
        tx`select app.create_notification(${workspaceB},${userB},${userA},'assigned','question',${questionB},${tenderB},${questionB},'spoof',null)`,
    ),
  ).rejects.toMatchObject({ code: '42501' });
});
it('streams original files only to their business', async () => {
  auth.user = userA;
  expect((await proxy(`/documents/${documentB}/file`)).status).toBe(404);
  auth.user = userB;
  const original = await proxy(`/documents/${documentB}/file`, 'GET', undefined, undefined, workspaceB);
  expect(original.status).toBe(200);
  expect(original.headers.get('content-disposition')).toContain('.xlsx');
  expect((await original.arrayBuffer()).byteLength).toBeGreaterThan(1000);
  auth.user = userA;
});
it('requires unique actor names and preserves append-only identity history', async () => {
  const duplicate = randomUUID();
  await sql`insert into auth.users values(${duplicate},${`${duplicate}@synthetic.test`},${sql.json({ display_name: 'Writer A' })})`;
  await expect(
    sql`insert into app.memberships(workspace_id,user_id,role) values(${workspaceA},${duplicate},'member')`,
  ).rejects.toMatchObject({ code: '23505' });
  await withUser(
    userA,
    (tx) =>
      tx`insert into app.activity_events(workspace_id,actor_id,type,object_type,summary) values(${workspaceA},${userA},'member.joined','membership','Synthetic identity event')`,
  );
  expect(await withUser(userA, (tx) => tx`delete from app.activity_events where workspace_id=${workspaceA} returning id`)).toEqual([]);
  expect(await withUser(userA, (tx) => tx`select id from app.activity_events where workspace_id=${workspaceA}`)).toHaveLength(1);
});
it('drafts, rejects stale edits and displacement, verifies and approves through the proxy', async () => {
  auth.user = userB;
  const call = (p: string, method = 'GET', body?: unknown) =>
    proxy(
      p,
      method,
      body === undefined ? undefined : JSON.stringify(body),
      body === undefined ? undefined : 'application/json',
      workspaceB,
    );
  const { consumeDraftStream } = await import('@/lib/backend-stream');
  const draft = await call(`/questions/${questionB}/draft`, 'POST', {});
  expect(draft.status).toBe(200);
  const events: string[] = [];
  await consumeDraftStream(draft.body!, (e) => {
    events.push(e.type);
  });
  expect(events.at(-1)).toBe('done');
  let q = await (await call(`/questions/${questionB}`)).json();
  const draftId = q.current_answer.id;
  const edited = await call(`/questions/${questionB}/answers`, 'POST', {
    text: 'Our named clinical safety officer reviews the safety case monthly.',
    base_version_id: draftId,
  });
  expect(edited.status).toBe(201);
  q = (await edited.json()).question;
  expect(q.status).toBe('writer_edited');
  expect(
    (
      await call(`/questions/${questionB}/answers`, 'POST', {
        text: 'Stale browser tab.',
        base_version_id: draftId,
      })
    ).status,
  ).toBe(409);
  expect((await call(`/questions/${questionB}/draft`, 'POST', {})).status).toBe(409);
  expect((await call(`/questions/${questionB}`, 'PATCH', { status: 'approved' })).status).toBe(409);
  for (const segment of q.current_answer.segments.filter((s: { support_status: string }) =>
    ['human_authored', 'weak', 'unsupported'].includes(s.support_status),
  )) {
    expect(
      (await call(`/answers/${q.current_answer.id}/segments/${segment.index}/attest`, 'POST', { note: 'Verified synthetic assertion.' }))
        .status,
    ).toBe(200);
  }
  q = await (await call(`/questions/${questionB}`)).json();
  for (const gap of q.current_answer.gaps)
    expect(
      (
        await call(`/questions/${questionB}/gaps/acknowledge`, 'POST', {
          gap,
          note: 'Synthetic test acknowledgement.',
        })
      ).status,
    ).toBe(200);
  const approved = await call(`/questions/${questionB}`, 'PATCH', {
    status: 'approved',
  });
  expect(approved.status, await approved.clone().text()).toBe(200);
  expect((await approved.json()).status).toBe('approved');
  expect(
    (
      await call(`/answers/${draftId}/segments/0/attest`, 'POST', {
        note: 'Old version.',
      })
    ).status,
  ).toBe(409);
  const reviewExport = await call(`/tenders/${tenderB}/export?format=docx&mode=review`);
  expect(reviewExport.status).toBe(200);
  expect((await reviewExport.arrayBuffer()).byteLength).toBeGreaterThan(1000);
  const submissionExport = await call(`/tenders/${tenderB}/export?format=xlsx&mode=submission`);
  expect(submissionExport.status).toBe(200);
  await submissionExport.body?.cancel();
  q = await (await call(`/questions/${questionB}`)).json();
  const supported = q.current_answer.segments.find((segment: { support_status: string }) => segment.support_status === 'supported');
  expect(supported).toBeTruthy();
  expect(
    (
      await call(`/answers/${q.current_answer.id}/segments/${supported.index}/dispute`, 'POST', {
        note: 'Synthetic evidence now disputed.',
      })
    ).status,
  ).toBe(200);
  expect((await call(`/tenders/${tenderB}/export?format=xlsx&mode=submission`)).status).toBe(409);
  expect(
    (await call(`/answers/${q.current_answer.id}/segments/${supported.index}/dispute`, 'POST', { note: 'Duplicate dispute.' })).status,
  ).toBe(409);
  auth.user = userA;
});
it('validates assignees and emits assignment, status and mention notifications', async () => {
  const colleague = randomUUID();
  await sql`insert into auth.users values(${colleague},${`${colleague}@synthetic.test`},${sql.json({ display_name: 'Reviewer B' })})`;
  await sql`insert into app.memberships(workspace_id,user_id,role) values(${workspaceB},${colleague},'member')`;
  auth.user = userB;
  const call = (body: unknown, suffix = '', method = 'PATCH') =>
    proxy(`/questions/${questionB}${suffix}`, method, JSON.stringify(body), 'application/json', workspaceB);
  expect((await call({ assignee: 'Writer A' })).status).toBe(400);
  expect((await call({ assignee: 'Reviewer B' })).status).toBe(200);
  expect((await call({ assignee: 'Reviewer B' })).status).toBe(200);
  expect((await call({ status: 'writer_edited' })).status).toBe(200);
  expect((await call({ text: '@Reviewer B please check this.' }, '/comments', 'POST')).status).toBe(201);
  const notes = await withUser(
    colleague,
    (tx) => tx`select event_type from app.notifications where workspace_id=${workspaceB} order by created_at`,
  );
  expect(notes.map((n) => n.eventType)).toEqual(['assigned', 'status_changed', 'mentioned']);
  expect(await withUser(userA, (tx) => tx`select * from app.notifications where workspace_id=${workspaceB}`)).toEqual([]);
  auth.user = userA;
});
it('keeps only form locations, validates ownership, and refuses unapproved answers', async () => {
  const { saveFormLocation, formLocations, filledForm } = await import('@/server/backend/forms');
  const target = { kind: 'xlsx_cell' as const, sheet: 'Questions', ref: 'F2' };
  await expect(
    saveFormLocation(userA, workspaceA, tenderB, {
      questionId: questionB,
      documentId: documentB,
      target,
    }),
  ).rejects.toMatchObject({ status: 404 });
  await saveFormLocation(userB, workspaceB, tenderB, {
    questionId: questionB,
    documentId: documentB,
    target,
  });
  expect(await formLocations(userB, workspaceB, tenderB)).toHaveLength(1);
  await expect(filledForm(userB, workspaceB, tenderB, documentB)).rejects.toMatchObject({ status: 409, body: { code: 'needs_review' } });
  auth.user = userB;
  const call = (p: string, method = 'GET', body?: unknown) =>
    proxy(
      p,
      method,
      body === undefined ? undefined : JSON.stringify(body),
      body === undefined ? undefined : 'application/json',
      workspaceB,
    );
  const q = await (await call(`/questions/${questionB}`)).json();
  for (const segment of q.current_answer.segments.filter((s: { support_status: string }) => s.support_status === 'unsupported'))
    await call(`/answers/${q.current_answer.id}/segments/${segment.index}/attest`, 'POST', { note: 'Rechecked.' });
  expect((await call(`/questions/${questionB}`, 'PATCH', { status: 'approved' })).status).toBe(200);
  expect(
    (
      await call(`/questions/${questionB}`, 'PATCH', {
        status: 'writer_edited',
      })
    ).status,
  ).toBe(200);
  await expect(filledForm(userB, workspaceB, tenderB, documentB)).rejects.toMatchObject({ code: 'EXPORT_BLOCKED' });
  auth.user = userA;
  await sql`insert into app.form_locations(workspace_id,tender_id,question_id,document_id,target,confirmed_by) values(${workspaceB},${tenderB},${randomUUID()},${documentB},${sql.json(target)},${userB})`;
  expect(await formLocations(userB, workspaceB, tenderB)).toHaveLength(1);
});

it('fills a confirmed empty cell from the approved backend version without copying content to the sidecar', async () => {
  const { saveFormLocation, filledForm } = await import('@/server/backend/forms');
  auth.user = userB;
  expect(
    (await proxy(`/questions/${questionB}`, 'PATCH', JSON.stringify({ status: 'approved' }), 'application/json', workspaceB)).status,
  ).toBe(200);
  await expect(filledForm(userB, workspaceB, tenderB, documentB)).rejects.toMatchObject({ code: 'EXPORT_BLOCKED' });
  await saveFormLocation(userB, workspaceB, tenderB, {
    questionId: questionB,
    documentId: documentB,
    target: { kind: 'xlsx_cell', sheet: 'Questions', ref: 'H2' },
  });
  const result = await filledForm(userB, workspaceB, tenderB, documentB);
  expect(result.applied).toBe(1);
  const { unzipSync, strFromU8 } = await import('fflate');
  const xml = strFromU8(unzipSync(result.bytes)['xl/worksheets/sheet1.xml']);
  expect(xml).toContain('Our named clinical safety officer reviews the safety case monthly.');
  expect(xml).toContain('Free text');
  // Kept for manual inspection in Excel or LibreOffice.
  await writeFile(path.join(tmpdir(), 'ten-filled-synthetic-form.xlsx'), result.bytes);
  auth.user = userA;
});
it('keeps reviewers and anchored comments inside the business, stores no answer text and notifies the right people', async () => {
  const c = await import('@/server/services/collaboration');
  const userC = randomUUID();
  await sql`insert into auth.users values (${userC}, ${`${userC}@synthetic.test`}, ${sql.json({ display_name: 'Reviewer C' })})`;
  await sql`insert into app.memberships (workspace_id, user_id, role) values (${workspaceB}, ${userC}, 'member')`;
  auth.user = userB;
  // Another business cannot reach this question, and reviewers must be members.
  await expect(c.addReviewer(userA, workspaceA, questionB, { userId: userC, role: 'sme' })).rejects.toMatchObject({ status: 404 });
  await expect(c.addReviewer(userB, workspaceB, questionB, { userId: userA, role: 'sme' })).rejects.toMatchObject({ code: 'VALIDATION_FAILED' });
  expect(await c.addReviewer(userB, workspaceB, questionB, { userId: userC, role: 'sme' })).toEqual({ added: true });
  expect(await c.addReviewer(userB, workspaceB, questionB, { userId: userC, role: 'sme' })).toEqual({ added: false });
  expect((await c.listTenderReviewers(userC, workspaceB, tenderB)).map((r) => [r.displayName, r.role])).toEqual([['Reviewer C', 'sme']]);
  expect(await withUser(userA, (tx) => tx`select * from app.question_reviewers where workspace_id = ${workspaceB}`)).toEqual([]);

  const detail = await (await proxy(`/questions/${questionB}`, 'GET', undefined, undefined, workspaceB)).json();
  const answerId = detail.current_answer.id;
  const anchor = { start: { segment: 0, offset: 0 }, end: { segment: 0, offset: 4 } };
  await expect(
    c.createCommentThread(userB, workspaceB, questionB, { answerId, anchor: { start: anchor.start, end: anchor.start }, body: 'Empty span' }),
  ).rejects.toMatchObject({ code: 'VALIDATION_FAILED' });
  await expect(c.createCommentThread(userB, workspaceB, questionB, { answerId: randomUUID(), anchor, body: 'Unknown version' })).rejects.toMatchObject({
    code: 'NOT_FOUND',
  });
  const { id } = await c.createCommentThread(userB, workspaceB, questionB, { answerId, anchor, body: 'Please check this, @Reviewer C' });
  const [row] = await sql`select * from app.comment_threads where id = ${id}`;
  expect(Object.keys(row).sort()).toEqual(
    ['anchor', 'answer_id', 'created_at', 'created_by', 'id', 'question_id', 'resolved_at', 'resolved_by', 'tender_id', 'workspace_id'].sort(),
  );
  await expect(c.listCommentThreads(userA, workspaceA, questionB)).rejects.toMatchObject({ status: 404 });
  await expect(c.replyToThread(userA, workspaceA, id, 'Not mine')).rejects.toMatchObject({ code: 'NOT_FOUND' });
  expect(await withUser(userA, (tx) => tx`select * from app.comment_messages where workspace_id = ${workspaceB}`)).toEqual([]);

  await c.replyToThread(userC, workspaceB, id, 'Checked.');
  await c.setThreadResolved(userC, workspaceB, id, true);
  const [thread] = await c.listCommentThreads(userB, workspaceB, questionB);
  expect(thread.messages.map((m) => [m.authorName, m.body])).toEqual([
    ['Writer B', 'Please check this, @Reviewer C'],
    ['Reviewer C', 'Checked.'],
  ]);
  expect(thread.resolvedAt).not.toBeNull();
  await expect(c.deleteCommentMessage(userC, workspaceB, thread.messages[0].id)).rejects.toMatchObject({ code: 'FORBIDDEN' });

  const received = async (user: string) =>
    (await withUser(user, (tx) => tx<{ eventType: string }[]>`select event_type from app.notifications where workspace_id = ${workspaceB} and recipient_id = ${user}`)).map(
      (n) => n.eventType,
    );
  expect(await received(userC)).toEqual(expect.arrayContaining(['review_requested', 'mentioned']));
  expect(await received(userB)).toContain('comment_added');
  auth.user = userA;
});

it('provisions organisations idempotently and never remaps an existing business', async () => {
  const { provisionOrganisation } = await import('@/server/backend/provision');
  const id = randomUUID();
  await sql`insert into app.workspaces(id,name) values(${id},'Synthetic provision test')`;
  await provisionOrganisation(id, 'Synthetic provision test');
  await provisionOrganisation(id, 'Synthetic provision test');
  const [row] = await sql`select backend_org_id from app.workspaces where id=${id}`;
  expect(row.backend_org_id).toBe(id);
  await expect(provisionOrganisation(workspaceA, 'Synthetic different organisation')).rejects.toThrow(/mapping/);
});
it('revokes backend access immediately when a membership is deactivated', async () => {
  await sql`update app.memberships set deactivated_at=now() where workspace_id=${workspaceA} and user_id=${userA}`;
  auth.user = userA;
  expect((await proxy('/tenders')).status).toBe(404);
});
