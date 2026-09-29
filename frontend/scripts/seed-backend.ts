// Synthetic development accounts and a backend-owned sample tender. No legacy
// tender content is inserted into the frontend database by the combined stack.
import { openAsBlob } from 'node:fs';
import path from 'node:path';
import { closeDb } from '../src/server/db';
import { backendConfig } from '../src/server/backend/config';
import { backendFetch } from '../src/server/backend/transport';
import { seedPeopleAndWorkspaces } from './seed-users';
import type { components } from '../src/server/backend/schema';

const identity = { actor: 'Development setup', orgId: '' };
type Schema = components['schemas'];

async function request<T>(resource: string, method = 'GET', body?: BodyInit, contentType?: string): Promise<T> {
  const response = await backendFetch(backendConfig(), identity, resource, { method, body, contentType });
  if (!response.ok) throw new Error(`Backend setup failed at ${resource}: ${response.status} ${await response.text()}`);
  return response.json();
}

async function main() {
  const { exampleHealth } = await seedPeopleAndWorkspaces();
  identity.orgId = exampleHealth;
  const existing = await request<Schema['DocumentWithJob'][]>('/documents');
  for (const filename of [
    'past_submission_westmoor_icb_2024.docx',
    'past_submission_harbourside_fT_2025.docx',
    'reference_iso27001_certificate_2024.docx',
    'reference_iso27001_certificate_2025.docx',
  ]) {
    let doc = existing.find((d) => d.filename === filename);
    if (!doc) {
      const form = new FormData();
      form.append('file', await openAsBlob(path.join(process.env.BACKEND_FIXTURES_DIR ?? '../eval/data', filename)), filename);
      doc = await request<Schema['DocumentWithJob']>('/documents', 'POST', form);
    }
    const deadline = Date.now() + 300_000;
    while (doc.ingest_status !== 'ready') {
      if (doc.ingest_status === 'failed') throw new Error(doc.ingest_error ?? 'Synthetic source ingestion failed.');
      if (Date.now() > deadline) throw new Error('Timed out awaiting the backend worker.');
      await new Promise((resolve) => setTimeout(resolve, 1000));
      doc = await request<Schema['DocumentWithJob']>(`/documents/${doc.id}`);
    }
    if (!doc.classification_confirmed) await request(`/documents/${doc.id}/confirm`, 'POST', JSON.stringify({}), 'application/json');
    console.log(`Synthetic library source ready: ${doc.filename}`);
  }
  const name = 'Northern Fells 2025 (synthetic)';
  const tenders = await request<Schema['TenderListItem'][]>('/tenders');
  const tender =
    tenders.find((t) => t.name === name) ??
    (await request<Schema['TenderDetail']>(
      '/tenders',
      'POST',
      JSON.stringify({ name, buyer: 'Northern Fells NHS Foundation Trust (fictional)', regime: 'procurement_act' }),
      'application/json',
    ));
  const detail = await request<Schema['TenderDetail']>(`/tenders/${tender.id}`);
  if (!(detail.documents ?? []).some((d) => d.tender_doc_kind === 'question_pack')) {
    const filename = 'question_pack_northern_fells_2025.xlsx';
    const form = new FormData();
    form.append('file', await openAsBlob(path.join(process.env.BACKEND_FIXTURES_DIR ?? '../eval/data', filename)), filename);
    form.append('tender_doc_kind', 'question_pack');
    await request(`/tenders/${tender.id}/documents`, 'POST', form);
  }
  console.log(`Synthetic backend tender ready for processing. Business: ${exampleHealth}`);
  console.log('Both synthetic businesses have separate backend organisations.');
}
main()
  .catch((error) => {
    console.error((error as Error).message);
    process.exitCode = 1;
  })
  .finally(() => closeDb());
