import 'server-only';
import { inWorkspace } from '../services/context';
import { backendForWorkspace, BackendError } from './client';
import { backendIdentity } from './context';
import { backendConfig } from './config';
import { backendFetch } from './transport';
import { AppError } from '../errors';
import { fillDocx, fillXlsx, type DocxTarget, type XlsxTarget } from '../exports/forms';
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** Route parameters reach backend paths, so anything but an ID is refused up front. */
function assertTenderId(tenderId: string) {
  if (!uuid.test(tenderId)) throw new AppError('NOT_FOUND', 'Tender not found.');
}
export type FormLocation = {
  id: string;
  questionId: string;
  documentId: string;
  target: DocxTarget | XlsxTarget;
};
export async function formLocations(userId: string, workspaceId: string, tenderId: string) {
  assertTenderId(tenderId);
  const client = await backendForWorkspace(userId, workspaceId);
  const tender = await client.get('/tenders/{tender_id}', {
    tender_id: tenderId,
  });
  const questions = await client.get('/tenders/{tender_id}/questions', {
    tender_id: tenderId,
  });
  const rows = await inWorkspace(
    userId,
    workspaceId,
    (tx) =>
      tx<
        FormLocation[]
      >`select id,question_id,document_id,target from app.form_locations where workspace_id=${workspaceId} and tender_id=${tenderId}`,
  );
  return rows.filter(
    (row) => questions.some((q) => q.id === row.questionId) && (tender.documents ?? []).some((d) => d.id === row.documentId),
  );
}
export async function saveFormLocation(
  userId: string,
  workspaceId: string,
  tenderId: string,
  input: {
    questionId: string;
    documentId: string;
    target: DocxTarget | XlsxTarget;
  },
) {
  assertTenderId(tenderId);
  const client = await backendForWorkspace(userId, workspaceId);
  const [question, document] = await Promise.all([
    client.get('/questions/{question_id}', { question_id: input.questionId }),
    client.get('/documents/{document_id}', { document_id: input.documentId }),
  ]);
  if (question.tender_id !== tenderId || document.tender_id !== tenderId)
    throw new AppError('NOT_FOUND', 'Question or form not found in this tender.');
  if (!document.filename.toLowerCase().endsWith(input.target.kind === 'xlsx_cell' ? '.xlsx' : '.docx'))
    throw new AppError('VALIDATION_FAILED', 'Choose a location matching the file type.');
  return inWorkspace(userId, workspaceId, async (tx) => {
    await tx`insert into app.form_locations (workspace_id,tender_id,question_id,document_id,target,confirmed_by) values (${workspaceId},${tenderId},${input.questionId},${input.documentId},${tx.json(input.target)},${userId}) on conflict(workspace_id,question_id,document_id) do update set target=excluded.target,confirmed_by=excluded.confirmed_by,confirmed_at=now()`;
    return { saved: true };
  });
}
export async function filledForm(userId: string, workspaceId: string, tenderId: string, documentId: string) {
  assertTenderId(tenderId);
  const identity = await backendIdentity(userId, workspaceId),
    config = backendConfig(),
    client = await backendForWorkspace(userId, workspaceId);
  // The authoritative export runs the fact-expiry sweep and refuses needs_review.
  // Only its status matters. Abort instead of cancelling the body: inside the Next.js server,
  // awaiting cancel() on a backend response body never settles.
  const gateRequest = new AbortController();
  const gate = await backendFetch(config, identity, `/tenders/${tenderId}/export?format=docx&mode=submission`, {
    signal: gateRequest.signal,
  });
  if (!gate.ok) throw new BackendError(gate.status, await gate.json());
  gateRequest.abort();
  const locations = (await formLocations(userId, workspaceId, tenderId)).filter((l) => l.documentId === documentId);
  if (!locations.length) throw new AppError('VALIDATION_FAILED', 'Confirm at least one answer location in this form first.');
  const document = await client.get('/documents/{document_id}', {
    document_id: documentId,
  });
  const fills = await Promise.all(
    locations.map(async (location) => {
      const q = await client.get('/questions/{question_id}', {
        question_id: location.questionId,
      });
      if (q.status !== 'approved' || q.needs_review || !q.current_answer)
        throw new AppError('EXPORT_BLOCKED', `Question ${q.number} needs an approved, current answer.`);
      return {
        target: location.target,
        text: q.current_answer.text,
        label: q.number,
      };
    }),
  );
  const targets = fills.map((f) => JSON.stringify(f.target));
  if (new Set(targets).size !== targets.length)
    throw new AppError('EXPORT_BLOCKED', 'Two answers point to the same cell. Correct their locations first.');
  const download = new AbortController();
  const original = await backendFetch(config, identity, `/documents/${documentId}/file`, { signal: download.signal });
  if (!original.ok) throw new BackendError(original.status, await original.json());
  const reader = original.body!.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.length;
    if (size > 25 * 1024 * 1024) {
      download.abort();
      throw new AppError('FILE_TOO_LARGE', 'The original form exceeds 25 MB.');
    }
    chunks.push(value);
  }
  const bytes = Buffer.concat(chunks);
  const result = document.filename.toLowerCase().endsWith('.xlsx')
    ? fillXlsx(bytes, fills as Array<{ target: XlsxTarget; text: string; label: string }>)
    : fillDocx(bytes, fills as Array<{ target: DocxTarget; text: string; label: string }>);
  if (result.skipped.length)
    throw new AppError(
      'EXPORT_BLOCKED',
      'Some locations are not empty or do not exist. Correct the confirmed locations before exporting.',
      result.skipped,
    );
  return {
    bytes: result.bytes,
    filename: `completed-${document.filename}`,
    applied: result.applied.length,
  };
}
