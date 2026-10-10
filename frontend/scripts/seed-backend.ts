// Synthetic library documents and the sample tender pack for the Example Health business.
// Runs alongside `web` (the `frontend-seed` service), after seed-provision.ts has created the
// accounts and organisations. Tolerant and re-runnable: a document whose filename already
// exists and did not fail is skipped, a Failed one is uploaded again, a document that fails is
// logged and the rest continue, waiting for the worker is bounded by one total deadline, and the
// run succeeds once the question pack is in.
// No legacy tender content is inserted into the frontend database by the combined stack.
import { openAsBlob } from 'node:fs';
import path from 'node:path';
import { closeDb, withService } from '../src/server/db';
import { backendConfig } from '../src/server/backend/config';
import { backendFetch } from '../src/server/backend/transport';
import { assertNotDemo } from './seed-users';
import type { components } from '../src/server/backend/schema';

type Schema = components['schemas'];
const identity = { actor: 'Development setup', orgId: '' };
const fixtures = process.env.BACKEND_FIXTURES_DIR ?? '../eval/data';
const LIBRARY_FILES = [
  'past_submission_westmoor_icb_2024.docx',
  'past_submission_harbourside_fT_2025.docx',
  'reference_iso27001_certificate_2024.docx',
  'reference_iso27001_certificate_2025.docx',
];
const TENDER_NAME = 'Northern Fells 2025 (synthetic)';
const PACK_FILE = 'question_pack_northern_fells_2025.xlsx';
// The buyer's specification beside the pack: once it is parsed, the worker scans the tender's
// documents for specification requirements and suggests a RAG rating for each (Specification tab).
const SPEC_FILE = 'specification_northern_fells_2025.docx';
// One deadline for every wait in the run, not one per document (live providers are slow).
const deadline = Date.now() + Number(process.env.SEED_WAIT_MS ?? 300_000);

const warn = (message: string) => console.warn(`Warning: ${message}`);

async function request<T>(resource: string, method = 'GET', body?: BodyInit, contentType?: string): Promise<T> {
  const response = await backendFetch(backendConfig(), identity, resource, { method, body, contentType });
  if (!response.ok) throw new Error(`${method} ${resource} failed: ${response.status} ${await response.text()}`);
  return response.json();
}

async function fileForm(filename: string, extra: Record<string, string> = {}): Promise<FormData> {
  const form = new FormData();
  form.append('file', await openAsBlob(path.join(fixtures, filename)), filename);
  for (const [key, value] of Object.entries(extra)) form.append(key, value);
  return form;
}

/** Polls the document until ready, failed or the shared deadline; returns the last state seen. */
async function awaitReady(doc: Schema['DocumentWithJob']): Promise<Schema['DocumentWithJob']> {
  while (!['ready', 'failed'].includes(doc.ingest_status) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    doc = await request<Schema['DocumentWithJob']>(`/documents/${doc.id}`);
  }
  return doc;
}

async function seedLibrary(): Promise<{ ready: number }> {
  const existing = await request<Schema['DocumentWithJob'][]>('/documents');
  let ready = 0;
  for (const filename of LIBRARY_FILES) {
    try {
      // GET /documents is newest-first, so this is the latest row for the filename.
      let doc = existing.find((d) => d.filename === filename);
      if (doc && doc.ingest_status !== 'failed') {
        // Already in the organisation and queued, processing or ready: leave it alone, except to
        // finish a confirmation an earlier run did not reach.
        if (doc.ingest_status === 'ready' && !doc.classification_confirmed) {
          await request(`/documents/${doc.id}/confirm`, 'POST', JSON.stringify({}), 'application/json');
          console.log(`Synthetic library source confirmed: ${filename}`);
        } else console.log(`Synthetic library source already present (${doc.ingest_status}): ${filename}`);
        if (doc.ingest_status === 'ready') ready += 1;
        continue;
      }
      // A Failed source is uploaded again (there is no re-ingest route); the earlier row stays in
      // the library as a record of the failure.
      if (doc) console.log(`Re-uploading failed source (${doc.ingest_error ?? 'see the worker log'}): ${filename}`);
      doc = await request<Schema['DocumentWithJob']>('/documents', 'POST', await fileForm(filename));
      doc = await awaitReady(doc);
      if (doc.ingest_status === 'failed') {
        warn(`${filename} failed to ingest: ${doc.ingest_error ?? 'see the worker log'}. Continuing.`);
        continue;
      }
      if (doc.ingest_status !== 'ready') {
        warn(`${filename} is still ${doc.ingest_status} after the wait; the worker continues in the background. Continuing.`);
        continue;
      }
      if (!doc.classification_confirmed) await request(`/documents/${doc.id}/confirm`, 'POST', JSON.stringify({}), 'application/json');
      ready += 1;
      console.log(`Synthetic library source ready: ${filename}`);
    } catch (error) {
      warn(`${filename}: ${(error as Error).message}. Continuing.`);
    }
  }
  return { ready };
}

async function seedTender(): Promise<void> {
  const tenders = await request<Schema['TenderListItem'][]>('/tenders');
  const tender =
    tenders.find((t) => t.name === TENDER_NAME) ??
    (await request<Schema['TenderDetail']>(
      '/tenders',
      'POST',
      JSON.stringify({ name: TENDER_NAME, buyer: 'Northern Fells NHS Foundation Trust (fictional)', regime: 'procurement_act' }),
      'application/json',
    ));
  const detail = await request<Schema['TenderDetail']>(`/tenders/${tender.id}`);
  const documents = detail.documents ?? [];
  if (documents.some((d) => d.tender_doc_kind === 'question_pack')) {
    console.log(`Synthetic question pack already uploaded: ${TENDER_NAME}`);
  } else {
    await request(`/tenders/${tender.id}/documents`, 'POST', await fileForm(PACK_FILE, { tender_doc_kind: 'question_pack' }));
    console.log(`Synthetic question pack uploaded: ${TENDER_NAME}. The worker extracts and triages it.`);
  }
  // Added to tenders seeded before the specification existed too; a failed upload is retried.
  if (documents.some((d) => d.tender_doc_kind === 'specification' && d.ingest_status !== 'failed')) {
    console.log(`Synthetic specification already uploaded: ${TENDER_NAME}`);
  } else {
    await request(`/tenders/${tender.id}/documents`, 'POST', await fileForm(SPEC_FILE, { tender_doc_kind: 'specification' }));
    console.log(`Synthetic specification uploaded: ${TENDER_NAME}. The worker scans it for requirements.`);
  }
}

async function main() {
  assertNotDemo();
  const [workspace] = await withService((tx) =>
    tx<{ id: string; backendOrgId: string | null }[]>`select id, backend_org_id from app.workspaces where name = 'Example Health (fictional)'`,
  );
  if (!workspace?.backendOrgId)
    throw new Error('The Example Health business is not provisioned yet. Run the frontend-provision service (scripts/seed-provision.ts) first.');
  identity.orgId = workspace.backendOrgId;
  const { ready } = await seedLibrary();
  if (ready < LIBRARY_FILES.length)
    warn(
      `${ready} of ${LIBRARY_FILES.length} library sources are ready. Coverage for the sample tender may read as new until they finish; use “Re-check coverage” on the tender afterwards, or rerun this seed.`,
    );
  // The pack decides the outcome: without it the sample tender has nothing to show.
  await seedTender();
  console.log(`Synthetic backend tender ready for processing. Business: ${workspace.id}`);
}
main()
  .catch((error) => {
    console.error((error as Error).message);
    process.exitCode = 1;
  })
  .finally(() => closeDb());
