import { expect, type Browser, type Page } from '@playwright/test';

export const PASSWORD = 'ten-dev-only';
export const USERS = {
  valerie: 'valerie@example-health.test',
  puru: 'puru@example-health.test',
  roger: 'roger@example-health.test',
  olive: 'olive@riverside-medical.test',
} as const;

/** Signs in within a fresh browser context, so each person has their own session. */
export async function signIn(browser: Browser, who: keyof typeof USERS, viewport = { width: 1440, height: 900 }): Promise<{ page: Page; workspaceId: string }> {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  await page.goto('/login');
  await page.fill('#email', USERS[who]);
  await page.fill('#password', PASSWORD);
  await page.click('button[type=submit]');
  await page.waitForURL(/\/w\/[0-9a-f-]{36}\//);
  const workspaceId = /\/w\/([0-9a-f-]{36})\//.exec(page.url())![1];
  return { page, workspaceId };
}

/** Reads backend state through the signed-in user's own proxy, as the browser does. */
export async function backendGet<T>(page: Page, workspaceId: string, path: string): Promise<T> {
  const response = await page.request.get(`/api/w/${workspaceId}/backend${path}`);
  expect(response.ok(), `GET ${path} returned ${response.status()}`).toBe(true);
  return response.json();
}

/** Creates a tender, uploads the synthetic question pack and opens its first DCB0129 question. */
export async function tenderWithDraftedQuestion(page: Page, workspaceId: string, name: string, pack: string) {
  await page.goto(`/w/${workspaceId}/tenders/new`);
  await page.getByLabel('Tender name').fill(name);
  await page.getByLabel('Buyer', { exact: true }).fill('Synthetic buyer');
  await page.getByRole('button', { name: 'Continue' }).click();
  await page.getByLabel('Choose question pack').setInputFiles(pack);
  await page.getByRole('button', { name: 'Create tender', exact: true }).click();
  await page.waitForURL(/\/tenders\/[0-9a-f-]{36}$/);
  const tenderUrl = page.url();
  await page
    .getByRole('link', { name: /DCB0129/ })
    .first()
    .click({ timeout: 90_000 });
  await page.waitForURL(/\/responses\/[0-9a-f-]{36}$/);
  const questionId = page.url().split('/').pop()!;
  await expect(page.getByRole('heading', { level: 1 })).toContainText('DCB0129');
  await page.getByRole('button', { name: 'Generate draft', exact: true }).click();
  await expect(page.getByText(/substantive sentences supported/)).toBeVisible({ timeout: 90_000 });
  return { tenderUrl, tenderId: tenderUrl.split('/').pop()!, questionId };
}

/** Confirms every flagged sentence, acknowledges every gap, then approves the answer. */
export async function verifyAndApprove(page: Page) {
  await page.getByText('Verify sentences', { exact: true }).click();
  for (const [button, note] of [
    ['Confirm accurate', 'Checked against the supplied synthetic evidence.'],
    ['Acknowledge gap', 'Acknowledged for the synthetic test.'],
  ] as const) {
    while (await page.getByRole('button', { name: button, exact: true }).count()) {
      const count = await page.getByRole('button', { name: button, exact: true }).count();
      await page.getByRole('button', { name: button, exact: true }).first().click();
      await page.getByRole('dialog').getByLabel('Note', { exact: true }).fill(note);
      await page.getByRole('dialog').getByRole('button', { name: 'Confirm', exact: true }).click();
      // The same sentence can be offered in the blocker list and the sentence list.
      await expect.poll(() => page.getByRole('button', { name: button, exact: true }).count()).toBeLessThan(count);
    }
  }
  await page.getByRole('button', { name: 'Approve', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Answer · Approved', exact: true })).toBeVisible();
}

export async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow, `page is ${overflow}px wider than the viewport`).toBeLessThanOrEqual(1);
}
