import path from 'node:path';
import { readFile } from 'node:fs/promises';
import { strFromU8, unzipSync } from 'fflate';
import { expect, test } from '@playwright/test';
import { backendGet, expectNoHorizontalScroll, PASSWORD, signIn, tenderWithDraftedQuestion, verifyAndApprove } from './support';
const pack = path.resolve(process.cwd(), '../eval/data/question_pack_northern_fells_2025.xlsx');
const packName = path.basename(pack);

type Question = { number: string; status: string; current_answer: { text: string } | null };
type Tender = { status: string; outcome: string; outcome_notes: string | null };

test('two signed-in colleagues upload, draft, verify, approve and export a synthetic question pack', async ({ browser }) => {
  const name = `Joined journey ${Date.now()}`;
  const { page: writer, workspaceId } = await signIn(browser, 'valerie');
  await writer.goto(`/w/${workspaceId}/library`);
  await expect(writer.getByRole('link', { name: 'past_submission_westmoor_icb_2024.docx', exact: true })).toBeVisible();
  const { tenderUrl } = await tenderWithDraftedQuestion(writer, workspaceId, name, pack);
  // The label wraps the select, so a label-text match would include the option text.
  const assignee = writer.getByRole('combobox', { name: 'Owner', exact: true });
  await assignee.selectOption('Puru');
  await expect(assignee).toHaveValue('Puru');
  const { page: reviewer } = await signIn(browser, 'puru');
  await reviewer.goto(`/w/${workspaceId}/notifications`);
  await expect(reviewer.getByText('Valerie assigned a question to you.', { exact: true }).first()).toBeVisible();
  await reviewer.goto(`/w/${workspaceId}/reviews`);
  await reviewer
    .getByRole('link', { name: new RegExp(name) })
    .first()
    .click();
  await verifyAndApprove(reviewer);
  // A competing browser edit must retain its local text and report a stale base.
  await writer.getByRole('button', { name: 'Edit answer', exact: true }).click();
  await writer.getByRole('textbox', { name: 'Edit answer', exact: true }).fill('Our team checks the clinical safety case every month.');
  await writer.getByRole('button', { name: 'Save answer', exact: true }).click();
  await expect(writer.getByRole('heading', { name: 'Answer · Writer edited', exact: true })).toBeVisible();
  await reviewer.getByRole('button', { name: 'Edit answer', exact: true }).click();
  await reviewer.getByRole('textbox', { name: 'Edit answer', exact: true }).fill('This is text from an older browser tab.');
  await reviewer.getByRole('button', { name: 'Save answer', exact: true }).click();
  await expect(reviewer.getByText('Another version was saved. Your text is still above.')).toBeVisible();
  await expect(reviewer.getByRole('textbox', { name: 'Edit answer', exact: true })).toHaveValue('This is text from an older browser tab.');
  await writer.goto(tenderUrl);
  await writer.getByRole('tab', { name: 'Submission', exact: true }).click();
  await writer.getByRole('button', { name: 'Export review DOCX', exact: true }).click();
  const download = writer.waitForEvent('download');
  await writer.getByRole('dialog').getByRole('button', { name: 'Download export' }).click();
  expect((await download).suggestedFilename()).toContain('.docx');
});

test('an assistant reply replaces an edited answer, fills the buyer’s form and the tender is submitted with an outcome', async ({
  browser,
}) => {
  const { page, workspaceId } = await signIn(browser, 'roger');
  const { tenderUrl, tenderId, questionId } = await tenderWithDraftedQuestion(page, workspaceId, `Forms journey ${Date.now()}`, pack);
  const versions = async () => (await backendGet<unknown[]>(page, workspaceId, `/questions/${questionId}/answers`)).length;

  // A hand edit moves the question past AI draft, so saving an assistant reply must ask first.
  await page.getByRole('button', { name: 'Edit answer', exact: true }).click();
  await page.getByRole('textbox', { name: 'Edit answer', exact: true }).fill('Our clinical safety officer reviews the safety case.');
  await page.getByRole('button', { name: 'Save answer', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Answer · Writer edited', exact: true })).toBeVisible();
  const before = await versions();
  await page.getByRole('tab', { name: 'Assistant' }).click();
  const saveReply = page.getByRole('button', { name: 'Save as answer', exact: true });
  await expect(page.getByRole('button', { name: 'Ask assistant', exact: true })).toBeVisible();
  const replies = await saveReply.count();
  await page.getByRole('textbox', { name: 'Instruction', exact: true }).fill('Make this shorter.');
  await page.getByRole('button', { name: 'Ask assistant', exact: true }).click();
  await expect(saveReply).toHaveCount(replies + 1, { timeout: 90_000 });
  await saveReply.last().click();
  await page
    .getByRole('dialog', { name: 'Replace the current answer?' })
    .getByRole('button', { name: 'Replace current answer', exact: true })
    .click();
  await expect(page.getByRole('heading', { name: 'Answer · AI draft', exact: true })).toBeVisible();
  await expect.poll(versions).toBe(before + 1);

  await verifyAndApprove(page);
  const question = await backendGet<Question>(page, workspaceId, `/questions/${questionId}`);
  expect(question.status).toBe('approved');

  // Confirm an empty answer cell in the original workbook and download the completed copy.
  await page.goto(`${tenderUrl}/submission`);
  await page.getByRole('combobox', { name: 'Original Word or Excel form', exact: true }).selectOption({ label: packName });
  const location = page.locator('form').filter({ hasText: `${question.number} · ` });
  await location.getByRole('textbox', { name: 'Worksheet', exact: true }).fill('Questions');
  await location.getByRole('textbox', { name: 'Answer cell', exact: true }).fill('H2');
  await location.getByRole('button', { name: 'Confirm location', exact: true }).click();
  await expect(page.locator('form').filter({ hasText: `${question.number} · ` })).toContainText('Location confirmed');
  const completed = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download completed form', exact: true }).click();
  const workbook = await readFile((await (await completed).path())!);
  const sheet = strFromU8(unzipSync(workbook)['xl/worksheets/sheet1.xml']);
  // Compare a stretch of plain words, since XML escapes some punctuation.
  const words = question.current_answer!.text.split(/\s+/).slice(0, 4).join(' ');
  expect(words).toMatch(/^[\w ]+$/);
  expect(sheet).toContain(words);

  // Export, mark submitted and record the outcome.
  await page.goto(tenderUrl);
  await page.getByRole('tab', { name: 'Submission', exact: true }).click();
  await page.getByRole('button', { name: 'Export submission DOCX', exact: true }).click();
  const exported = page.waitForEvent('download');
  await page.getByRole('dialog').getByRole('button', { name: 'Download export' }).click();
  expect((await exported).suggestedFilename()).toContain('.docx');
  page.once('dialog', (dialog) => void dialog.accept());
  await page.getByRole('button', { name: 'Mark submitted', exact: true }).click();
  // Submission runs as a backend job that also promotes approved answers into the library.
  await expect.poll(async () => (await backendGet<Tender>(page, workspaceId, `/tenders/${tenderId}`)).status, { timeout: 60_000 }).toBe('submitted');
  await page.reload();
  await page.getByRole('tab', { name: 'Submission', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Mark submitted', exact: true })).toBeDisabled();
  await page.getByRole('combobox', { name: 'Outcome', exact: true }).selectOption('won');
  await page.getByRole('textbox', { name: 'Outcome notes', exact: true }).fill('Synthetic outcome.');
  await page.getByRole('button', { name: 'Save outcome', exact: true }).click();
  await expect
    .poll(async () => {
      const tender = await backendGet<Tender>(page, workspaceId, `/tenders/${tenderId}`);
      return [tender.outcome, tender.outcome_notes];
    })
    .toEqual(['won', 'Synthetic outcome.']);
});

test('an owner adds an SME reviewer and discusses selected words of the answer with them', async ({ browser }) => {
  const name = `Comments journey ${Date.now()}`;
  const { page: owner, workspaceId } = await signIn(browser, 'valerie');
  const { tenderUrl, questionId } = await tenderWithDraftedQuestion(owner, workspaceId, name, pack);
  const question = await backendGet<Question>(owner, workspaceId, `/questions/${questionId}`);

  await owner.getByRole('combobox', { name: 'Reviewer', exact: true }).selectOption({ label: 'Puru' });
  await owner.getByRole('combobox', { name: 'Reviewer role', exact: true }).selectOption({ label: 'SME reviewer' });
  await owner.getByRole('button', { name: 'Add reviewer', exact: true }).click();
  await expect(owner.getByRole('button', { name: 'Remove Puru as SME reviewer' })).toBeVisible();

  // Select the first words of the second sentence, as a person dragging over them would.
  const words = await owner.evaluate(() => {
    const target = document.querySelector('[data-seg-text="1"]')!;
    const walker = document.createTreeWalker(target, NodeFilter.SHOW_TEXT);
    const node = walker.nextNode()!;
    const range = document.createRange();
    range.setStart(node, 0);
    range.setEnd(node, Math.min(24, node.textContent!.length));
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
    return range.toString();
  });
  await owner.locator('[data-seg-text="1"]').dispatchEvent('mouseup');
  await owner.getByRole('button', { name: 'Comment', exact: true }).click();
  await expect(owner.getByRole('tab', { name: /Comments/ })).toHaveAttribute('aria-selected', 'true');
  const composer = owner.getByRole('combobox', { name: 'New comment' });
  await composer.pressSequentially('Can we name the officer here? @Pu');
  await owner.getByRole('option', { name: 'Puru' }).click();
  await expect(composer).toHaveValue('Can we name the officer here? @Puru ');
  await expect(owner.getByText('Will notify Puru.')).toBeVisible();
  await owner.getByRole('button', { name: 'Comment', exact: true }).click();
  const thread = owner.getByRole('article', { name: 'Comment by Valerie' });
  await expect(thread).toContainText(words);
  await expect(thread.getByText('@Puru', { exact: true })).toBeVisible();
  await expect(owner.locator('mark[data-threads]')).toHaveText(words);

  const { page: reviewer } = await signIn(browser, 'puru');
  await reviewer.goto(`/w/${workspaceId}/notifications`);
  await expect(reviewer.getByText('Valerie mentioned you in a comment.', { exact: true }).first()).toBeVisible();
  await expect(reviewer.getByText(`Valerie asked you to be the SME reviewer for question ${question.number}.`, { exact: true }).first()).toBeVisible();
  // My tasks: the question is the SME's turn, and the mention is a reply the SME owes.
  await reviewer.goto(`/w/${workspaceId}/tasks`);
  const turn = reviewer.getByRole('region', { name: 'Your turn' }).getByRole('link', { name: new RegExp(name) });
  await expect(turn).toContainText('SME reviewer');
  await expect(turn).toContainText('Verify the answer as SME reviewer');
  const owed = reviewer.getByRole('region', { name: 'Replies waiting for you' }).getByRole('link', { name: new RegExp(name) });
  await expect(owed).toContainText('Mentioned you');
  await expect(owed).toContainText('Valerie: Can we name the officer here? @Puru');
  await owed.click();
  await expect(reviewer.getByRole('tab', { name: /Comments/ })).toHaveAttribute('aria-selected', 'true');
  const questionPage = reviewer.url().split('?')[0];
  const shared = reviewer.getByRole('article', { name: 'Comment by Valerie' });
  await shared.getByRole('combobox', { name: 'Reply' }).fill('Added below.');
  await shared.getByRole('button', { name: 'Reply', exact: true }).click();
  await expect(shared).toContainText('Added below.');
  // Once answered, the reply is owed by Valerie instead.
  await reviewer.goto(`/w/${workspaceId}/tasks`);
  await expect(reviewer.getByRole('region', { name: 'Replies waiting for you' }).getByRole('link', { name: new RegExp(name) })).toHaveCount(0);
  const ownerTasks = await owner.context().newPage();
  await ownerTasks.goto(`/w/${workspaceId}/tasks`);
  await expect(ownerTasks.getByRole('region', { name: 'Replies waiting for you' }).getByRole('link', { name: new RegExp(name) })).toContainText(
    'Puru: Added below.',
  );
  await ownerTasks.close();
  await reviewer.goto(questionPage);

  // A rewrite that drops the commented words leaves the thread outdated rather than misplaced.
  await owner.getByRole('button', { name: 'Edit answer', exact: true }).click();
  await owner.getByRole('textbox', { name: 'Edit answer', exact: true }).fill('Our named clinical safety lead signs off every release.');
  await owner.getByRole('button', { name: 'Save answer', exact: true }).click();
  await expect(owner.getByRole('heading', { name: 'Answer · Writer edited', exact: true })).toBeVisible();
  await expect(thread).toContainText('The text changed since this comment');
  await expect(owner.locator('mark[data-threads]')).toHaveCount(0);

  await reviewer.reload();
  await reviewer.getByRole('tab', { name: /Comments/ }).click();
  await reviewer.getByRole('article', { name: 'Comment by Valerie' }).getByRole('button', { name: 'Resolve', exact: true }).click();
  await expect(reviewer.getByText('Resolved (1)')).toBeVisible();
  void tenderUrl;
});

test('an admin invites a new colleague who sets a password and works on the business’s tenders', async ({ browser }) => {
  const stamp = Date.now();
  const email = `new.colleague.${stamp}@example-health.test`;
  const name = `Nadia ${stamp}`;
  const { page: admin, workspaceId } = await signIn(browser, 'valerie');
  await admin.goto(`/w/${workspaceId}/team`);
  await admin.getByRole('button', { name: 'Add a person' }).click();
  await admin.getByLabel('Email').fill(email);
  await admin.getByLabel('Name (for new accounts)').fill(name);
  await admin.getByRole('button', { name: 'Add', exact: true }).click();
  await expect(admin.getByText(`Invitation link for ${email}`, { exact: true })).toBeVisible();
  const link = (await admin.getByText(/\/auth\/confirm\?/).textContent())!.trim();
  expect(link).toMatch(/^http/);

  const invitee = await (await browser.newContext()).newPage();
  await invitee.goto(link);
  await invitee.getByLabel('New password').fill(`${PASSWORD}-${stamp}`);
  await invitee.getByLabel('Confirm password').fill(`${PASSWORD}-${stamp}`);
  await invitee.getByRole('button', { name: 'Save password and continue' }).click();
  await invitee.waitForURL(new RegExp(`/w/${workspaceId}/`));
  // The new account reaches backend data under its own display name.
  await invitee.goto(`/w/${workspaceId}/tenders`);
  await expect(invitee.getByRole('link', { name: /Northern Fells 2025 \(synthetic\)/ }).first()).toBeVisible();
  await admin.reload();
  await expect(admin.getByText(name, { exact: true }).first()).toBeVisible();
});

test('the development user switch signs in as another test account', async ({ browser }) => {
  const { page } = await signIn(browser, 'valerie');
  await page.getByRole('button', { name: 'Account menu' }).click();
  await page.getByRole('menuitem', { name: 'Switch to Sam' }).click();
  await page.waitForURL(/\/w\/[0-9a-f-]{36}\//);
  await expect(page.getByRole('button', { name: 'Account menu' })).toContainText('Sam');
  await page.getByRole('button', { name: 'Account menu' }).click();
  await expect(page.getByText('Workspace member', { exact: true })).toBeVisible();
});

test('a tender can be renamed, archived, restored and deleted', async ({ browser }) => {
  const { page, workspaceId } = await signIn(browser, 'puru');
  const name = `Manage journey ${Date.now()}`;
  const created = await page.request.post(`/api/w/${workspaceId}/backend/tenders`, {
    headers: { origin: 'http://localhost:3000', 'x-ten-request': '1' },
    data: { name },
  });
  expect(created.status()).toBe(201);
  const { id } = await created.json();
  await page.goto(`/w/${workspaceId}/tenders/${id}`);
  await expect(page.getByRole('heading', { name: 'Upload the buyer’s question pack' })).toBeVisible();

  await page.getByRole('button', { name: 'Manage tender' }).click();
  await page.getByRole('menuitem', { name: 'Edit details' }).click();
  const dialog = page.getByRole('dialog', { name: 'Edit tender details' });
  await dialog.getByLabel('Tender name').fill(`${name} renamed`);
  await dialog.getByLabel('Buyer').fill('Synthetic Trust');
  await dialog.getByLabel('Submission deadline (your local time)').fill('2030-01-15T12:00');
  await dialog.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(`${name} renamed`);
  await expect(page.getByText(/Synthetic Trust · Deadline 15 Jan 2030/)).toBeVisible();

  await page.getByRole('button', { name: 'Manage tender' }).click();
  await page.getByRole('menuitem', { name: 'Archive' }).click();
  await expect(page.getByText('This tender is archived.')).toBeVisible();
  await page.goto(`/w/${workspaceId}/tenders`);
  await expect(page.getByRole('link', { name: new RegExp(`${name} renamed`) })).toHaveCount(0);
  await page.getByRole('tab', { name: /Archived/ }).click();
  await page.getByRole('link', { name: new RegExp(`${name} renamed`) }).click();
  await page.getByRole('button', { name: 'Restore', exact: true }).click();
  await expect(page.getByText('This tender is archived.')).toHaveCount(0);

  await page.getByRole('button', { name: 'Manage tender' }).click();
  await page.getByRole('menuitem', { name: 'Delete tender' }).click();
  const confirm = page.getByRole('dialog', { name: 'Delete this tender?' });
  await expect(confirm.getByRole('button', { name: 'Delete tender' })).toBeDisabled();
  await confirm.getByRole('textbox').fill(`${name} renamed`);
  await confirm.getByRole('button', { name: 'Delete tender' }).click();
  await page.waitForURL(new RegExp(`/w/${workspaceId}/tenders$`));
  expect((await page.request.get(`/api/w/${workspaceId}/backend/tenders/${id}`)).status()).toBe(404);
});

test('the board groups questions and moves them by drag and drop within the review rules', async ({ browser }) => {
  const { page, workspaceId } = await signIn(browser, 'valerie');
  await page.goto(`/w/${workspaceId}/tenders`);
  await page.getByRole('link', { name: /Northern Fells 2025 \(synthetic\)/ }).first().click();
  await page.waitForURL(/\/tenders\/[0-9a-f-]{36}$/);
  const notStarted = page.getByRole('region', { name: 'Not started' });
  // An unanswered question cannot be approved: the move is refused with an explanation.
  await notStarted.getByRole('link').first().dragTo(page.getByRole('region', { name: 'Approved' }));
  await expect(page.getByRole('alert').filter({ hasText: 'can’t move' })).toContainText(/Question [\d.]+ can’t move to Approved yet/);
  await expect(page.getByRole('region', { name: 'Approved' }).getByRole('link')).toHaveCount(0);

  await page.getByRole('combobox', { name: 'Group by' }).selectOption('owner');
  const sam = page.getByRole('region', { name: 'Sam' });
  const before = await sam.getByRole('link').count();
  const unowned = page.getByRole('region', { name: 'No owner' }).getByRole('link').first();
  const text = (await unowned.locator('h3').textContent())!;
  await unowned.dragTo(sam);
  await expect(sam).toContainText(text);
  await expect(sam.getByRole('link')).toHaveCount(before + 1);

  await page.getByRole('tab', { name: 'Table', exact: true }).click();
  await expect(page.getByRole('cell', { name: /Sam/ }).first()).toBeVisible();
  await page.getByRole('combobox', { name: 'Owner filter' }).selectOption('Sam');
  await expect(page.getByText(new RegExp(`^${before + 1} of \\d+ questions$`))).toBeVisible();
});

test('business isolation and signed-out navigation remain enforced', async ({ browser }) => {
  const { page: writer, workspaceId } = await signIn(browser, 'valerie');
  await writer.close();
  const { page: outsider } = await signIn(browser, 'olive');
  expect((await outsider.goto(`/w/${workspaceId}/tenders`))?.status()).toBe(404);
  expect((await outsider.request.get(`/api/w/${workspaceId}/backend/tenders`)).status()).toBe(404);
  const anon = await (await browser.newContext()).newPage();
  await anon.goto(`/w/${workspaceId}/tenders?persona=valerie&as=admin`);
  await expect(anon).toHaveURL(/\/login/);
});
for (const width of [1440, 1024, 768, 375])
  test(`joined screens fit ${width}px`, async ({ browser }) => {
    const { page, workspaceId } = await signIn(browser, 'valerie', { width, height: 900 });
    await page.goto(`/w/${workspaceId}/tenders`);
    await page
      .getByRole('link', { name: /Northern Fells 2025 \(synthetic\)/ })
      .first()
      .click();
    await page.waitForURL(/\/tenders\/[0-9a-f-]{36}$/);
    const base = page.url();
    for (const view of ['Board', 'Table', 'Document', 'Buyer documents', 'Submission']) {
      await page.getByRole('tab', { name: view, exact: true }).click();
      await expectNoHorizontalScroll(page);
    }
    await page.goto(`${base}/submission`);
    await expect(page.getByRole('heading', { name: 'Fill the buyer’s form' })).toBeVisible();
    await expectNoHorizontalScroll(page);
    for (const target of ['reviews', 'library', 'team', 'notifications']) {
      await page.goto(`/w/${workspaceId}/${target}`);
      await expect(page.locator('h1').first()).toBeVisible();
      await expectNoHorizontalScroll(page);
    }
  });
