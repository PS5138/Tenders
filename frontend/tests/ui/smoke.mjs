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
  documents: [],
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
const entry = `import React from 'react';import {createRoot} from 'react-dom/client';import {AppShell} from './src/features/shell/app-shell';import {QuestionWorkspace} from './src/features/backend/question';import {TenderWorkspace} from './src/features/backend/tenders';import {Library} from './src/features/backend/library';import {BuyerForms} from './src/features/backend/forms';const screen=new URLSearchParams(location.search).get('screen');const props={workspaceId:${JSON.stringify(ws)},tenderId:${JSON.stringify(q.tender_id)}};createRoot(document.getElementById('root')).render(<AppShell shell={${JSON.stringify(shell)}} ai={{status:'ok',llm_provider:'fake',embedding_provider:'fake',synthetic_demo:true}}>{screen==='tender'?<TenderWorkspace {...props}/>:screen==='library'?<Library workspaceId={props.workspaceId}/>:screen==='forms'?<BuyerForms {...props}/>:<QuestionWorkspace {...props} questionId=${JSON.stringify(q.id)} members={[{userId:props.workspaceId,displayName:'Test writer'}]}/>}</AppShell>);`;
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
        build.onResolve({ filter: /^next\/(link|navigation)$/ }, (args) => ({ path: args.path, namespace: 'stub' }));
        build.onLoad({ filter: /.*/, namespace: 'stub' }, (args) => ({
          contents:
            args.path === 'next/link'
              ? `import React from 'react';export default function Link({href,children,...props}){return React.createElement('a',{href,...props},children)}`
              : `export const useRouter=()=>({push:()=>{},refresh:()=>{}});export const usePathname=()=>'/w/${ws}/tenders';`,
          loader: 'js',
          resolveDir: root,
        }));
      },
    },
  ],
});
const cssDir = path.join(root, '.next/static/css');
const css = (
  await Promise.all((await readdir(cssDir)).filter((f) => f.endsWith('.css')).map((f) => readFile(path.join(cssDir, f), 'utf8')))
).join('\n');
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
          data: url.pathname.endsWith('/forms')
            ? []
            : url.pathname.endsWith('/notifications')
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
        await page
          .getByRole('button', { name: /Sentence 1:/ })
          .first()
          .waitFor();
        await page
          .getByRole('button', { name: /Sentence 1:/ })
          .first()
          .click();
        const source = page.getByRole('dialog').getByRole('button').first();
        if (await source.count()) {
          await source.click();
          await page.getByRole('complementary', { name: 'Source section' }).waitFor();
        }
        await page.keyboard.press('Escape');
      }
      if (screen === 'tender') {
        await page.getByRole('button', { name: 'table', exact: true }).click();
        await page.locator('table').waitFor();
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
    `${checks} browser rendering checks passed (four screens × four widths). Sign-in and mutations are not covered by this harness.`,
  );
} finally {
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
  await rm(temp, { recursive: true, force: true });
}
