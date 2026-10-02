// Rendering check only. Uses the real backend fixture, mocked navigation and API
// snapshots. The signed-in Playwright journeys remain a separate acceptance gate.
import { createRequire } from 'node:module';
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import http from 'node:http';
import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';
const require = createRequire(import.meta.url);
const { build } = createRequire(require.resolve('tsx/package.json'))('esbuild');
const root = process.cwd(),
  temp = await mkdtemp(path.join(tmpdir(), 'ten-ui-smoke-'));
const backend = process.env.BACKEND_URL ?? 'http://127.0.0.1:8000';
const headers = process.env.BACKEND_SERVICE_SECRET ? { authorization: `Bearer ${process.env.BACKEND_SERVICE_SECRET}` } : {};
const health = await (await fetch(`${backend}/health`, { headers })).json();
assert.equal(health.llm_provider, 'fake', 'Rendering smoke requires a synthetic local backend.');
const fixture = await (await fetch(`${backend}/fixtures/answer`, { headers })).json();
const q = fixture.question,
  ws = '11111111-1111-4111-8111-111111111111';
const tender = {
  id: q.tender_id,
  name: 'Synthetic tender rendering check',
  buyer: 'Synthetic buyer',
  status: 'open',
  outcome: 'pending',
  questions_total: 1,
  questions_approved: 0,
  words_total: fixture.answer.word_count,
  words_approved: 0,
  needs_review_count: 1,
  c_count: 0,
  unclassified_mandatory_count: 0,
  // A question pack must be present for the workspace to show its views rather than the upload step.
  documents: [
    {
      id: '00000000-0000-4000-8000-0000000000d1',
      filename: 'question_pack.xlsx',
      tender_doc_kind: 'question_pack',
      ingest_status: 'ready',
      doc_type: null,
      classification_confirmed: false,
      created_at: new Date().toISOString(),
    },
  ],
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};
const shell = {
  workspace: { id: ws, name: 'Synthetic business' },
  workspaces: [{ id: ws, name: 'Synthetic business', role: 'admin' }],
  me: { userId: ws, displayName: 'Test writer', email: 'writer@synthetic.test', role: 'admin' },
  openReviewCount: 1,
  unreadNotificationCount: 0,
};
const entry = `import React from 'react';import {createRoot} from 'react-dom/client';import {AppShell} from './src/features/shell/app-shell';import {QuestionWorkspace} from './src/features/backend/question';import {TenderWorkspace} from './src/features/backend/tenders';import {Library} from './src/features/backend/library';import {BuyerForms} from './src/features/backend/forms';const screen=new URLSearchParams(location.search).get('screen');const props={workspaceId:${JSON.stringify(ws)},tenderId:${JSON.stringify(q.tender_id)}};const members=[{userId:props.workspaceId,displayName:'Test writer'}];createRoot(document.getElementById('root')).render(<AppShell shell={${JSON.stringify(shell)}} ai={{status:'ok',llm_provider:'fake',embedding_provider:'fake',synthetic_demo:true}}>{screen==='tender'?<TenderWorkspace {...props} members={members}/>:screen==='library'?<Library workspaceId={props.workspaceId}/>:screen==='forms'?<BuyerForms {...props}/>:<QuestionWorkspace {...props} questionId=${JSON.stringify(q.id)} members={members}/>}</AppShell>);`;
await build({
  stdin: { contents: entry, resolveDir: root, sourcefile: 'ui-smoke.tsx', loader: 'tsx' },
  bundle: true,
  jsx: 'automatic',
  outfile: path.join(temp, 'app.js'),
  define: { 'process.env.NODE_ENV': '"development"' },
  plugins: [
    {
      name: 'navigation-stub',
      setup(build) {
        // AppShell is a server component that reads APP_URL; in this browser bundle the configured
        // origin is the harness's own, so the address-mismatch notice stays out of the rendering checks.
        build.onResolve({ filter: /^(next\/(link|navigation)|server-only|@\/server\/env)$/ }, (args) => ({ path: args.path, namespace: 'stub' }));
        build.onLoad({ filter: /.*/, namespace: 'stub' }, (args) => ({
          contents:
            args.path === 'next/link'
              ? `import React from 'react';export default function Link({href,children,...props}){return React.createElement('a',{href,...props},children)}`
              : args.path === 'server-only'
                ? ''
                : args.path === '@/server/env'
                  ? `export const env=()=>({APP_URL:location.origin});`
                  : `export const useRouter=()=>({push:()=>{},refresh:()=>{}});export const usePathname=()=>'/w/${ws}/tenders';`,
          loader: 'js',
          resolveDir: root,
        }));
      },
    },
  ],
});
// Webpack builds write stylesheets to .next/static/css, Turbopack builds to .next/static/chunks.
const cssFiles = (
  await Promise.all(
    ['.next/static/css', '.next/static/chunks'].map(async (dir) => {
      const full = path.join(root, dir);
      const names = await readdir(full).catch(() => []);
      return names.filter((f) => f.endsWith('.css')).map((f) => path.join(full, f));
    }),
  )
).flat();
assert(cssFiles.length, 'No stylesheet found under .next/static; run `pnpm build` first.');
const css = (await Promise.all(cssFiles.map((f) => readFile(f, 'utf8')))).join('\n');
const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/app.js') {
      res.setHeader('content-type', 'text/javascript');
      res.end(await readFile(path.join(temp, 'app.js')));
      return;
    }
    if (url.pathname === '/style.css') {
      res.setHeader('content-type', 'text/css');
      res.end(css);
      return;
    }
    if (url.pathname.startsWith('/api/')) {
      let data = [];
      const endpoint = url.pathname.split('/backend')[1];
      if (endpoint === `/questions/${q.id}`) data = q;
      else if (endpoint === `/questions/${q.id}/answers`) data = [fixture.answer];
      else if (endpoint === `/threads/${q.thread_id}`) data = fixture.thread;
      else if (endpoint === `/tenders/${q.tender_id}`) data = tender;
      else if (endpoint === `/tenders/${q.tender_id}/questions`) data = [q];
      else if (endpoint?.startsWith('/sections/')) data = await (await fetch(`${backend}${endpoint}${url.search}`, { headers })).json();
      else if (!endpoint)
        data = {
          ok: true,
          data: ['/forms', '/notifications', '/comments', '/reviewers'].some((suffix) => url.pathname.endsWith(suffix))
            ? []
            : { openReviewCount: 1, unreadNotificationCount: 0 },
        };
      res.setHeader('content-type', 'application/json');
      res.end(JSON.stringify(data));
      return;
    }
    res.setHeader('content-type', 'text/html');
    res.end(
      '<!doctype html><html data-theme="light"><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/style.css"></head><body><div id="root"></div><script src="/app.js"></script></body></html>',
    );
  } catch (error) {
    res.statusCode = 500;
    res.end(error.message);
  }
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({
  executablePath: process.env.PW_CHROMIUM_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  headless: true,
});
const errors = [];
let checks = 0;
try {
  for (const width of [1440, 1024, 768, 375]) {
    const page = await browser.newPage({ viewport: { width, height: 1000 } });
    page.on('pageerror', (error) => errors.push(error.message));
    for (const screen of ['question', 'tender', 'library', 'forms']) {
      await page.goto(`http://127.0.0.1:${server.address().port}/?screen=${screen}`);
      await page.locator('h1').first().waitFor();
      if (screen === 'question') {
        // Sentence 1 of the fixture has a located document source. Hover shows its popover with no click,
        // and the popover is read from the segment record, so no section request is made.
        const marker = page.getByRole('button', { name: /^Sentence 1:/ }).first();
        await marker.waitFor();
        const sectionRequests = [];
        page.on('request', (request) => {
          if (request.url().includes('/sections/')) sectionRequests.push(request.url());
        });
        await marker.hover();
        const popover = page.getByRole('dialog', { name: 'Source for sentence 1' });
        await popover.waitFor();
        await popover.getByRole('button', { name: /^Verify source/ }).first().waitFor();
        assert.equal(sectionRequests.length, 0, 'hovering a sentence must not request its section');
        // Clicking the marker opens the source pane directly, scrolled so the highlighted span is in view.
        await marker.click();
        const pane = page.getByRole('complementary', { name: 'Source section' });
        await pane.waitFor();
        const mark = pane.locator('mark');
        await mark.waitFor();
        const inView = await mark.evaluate((el) => {
          const box = el.closest('[data-testid="source-scroll"]').getBoundingClientRect();
          const m = el.getBoundingClientRect();
          return m.top >= box.top - 1 && m.bottom <= box.bottom + 1;
        });
        assert(inView, 'the source pane scrolls to the highlighted span');
        await page.keyboard.press('Escape');
        // The n key jumps to the next sentence needing attention and focuses its marker.
        await page.keyboard.press('n');
        const focused = await page.evaluate(() => document.activeElement?.getAttribute('aria-label') ?? '');
        assert.match(
          focused,
          /^Sentence \d+: (Weak|No supporting|Disputed|Written by a person)/,
          `n must focus a sentence needing attention, focused "${focused}"`,
        );
        // Keyboard route to every source: focus opens the popover, the down arrow moves into it, so Tab
        // reaches each "Verify source" button, and Escape brings the keyboard back to the marker.
        await marker.focus();
        await popover.waitFor();
        await page.keyboard.press('ArrowDown');
        const inPopover = await page.evaluate(() => document.activeElement?.getAttribute('aria-label') ?? '');
        assert.match(inPopover, /^Verify source/, `the down arrow must focus a Verify source button, focused "${inPopover}"`);
        await page.keyboard.press('Escape');
        await popover.waitFor({ state: 'hidden' });
        assert(await marker.evaluate((el) => el === document.activeElement), 'Escape from the popover must return focus to the marker');
      }
      if (screen === 'tender') {
        await page.getByRole('tab', { name: 'List', exact: true }).click();
        await page.locator('main section ul li').first().waitFor();
      }
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
      assert(overflow <= 1, `${screen} overflows ${width}px by ${overflow}px`);
      checks++;
      if (width === 1440 && screen === 'question' && process.env.UI_SMOKE_SCREENSHOT)
        await page.screenshot({ path: process.env.UI_SMOKE_SCREENSHOT, fullPage: true });
    }
    await page.close();
  }
  assert.deepEqual(errors, [], 'Browser runtime errors');
  console.log(
    `${checks} browser rendering checks passed (four screens × four widths, with the hover, click-to-source, pane scroll, n-key and popover keyboard trace checks on the question screen). Sign-in and mutations are not covered by this harness.`,
  );
} finally {
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
  await rm(temp, { recursive: true, force: true });
}
