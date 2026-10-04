'use client';
import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { backendJson, backendRequest } from '@/lib/backend-api';
import type { components } from '@/server/backend/schema';
import { Button } from '@/components/ui/button';
import { JOB_POLL_INTERVAL_MS, jobRetryDelay, shouldShowPollError } from '@/lib/job-poll';
import Link from 'next/link';
export type S = components['schemas'];
export const field = 'w-full rounded-md border border-line bg-bg px-3 py-2 text-sm text-ink';
export const panel = 'min-w-0 rounded-lg border border-line bg-bg p-4';
export { label } from './labels';
import { label } from './labels';
export function ErrorNote({ error }: { error: string | null | undefined }) {
  return error ? (
    <p role="alert" className="my-3 rounded bg-red-bg p-3 text-sm text-red wrap-anywhere">
      {error}
    </p>
  ) : null;
}
export function Field({ title, children }: { title: string; children: ReactNode }) {
  return (
    <label className="block space-y-1 text-xs text-muted">
      <span>{title}</span>
      {children}
    </label>
  );
}
export function useResource<T>(workspaceId: string, path: string) {
  const key = `${workspaceId}:${path}`;
  const [snapshot, setSnapshot] = useState<{ key: string; data: T | null; error: string | null }>({ key, data: null, error: null });
  const reload = useCallback(async () => {
    try {
      const next = await backendJson<T>(workspaceId, path);
      setSnapshot({ key, data: next, error: null });
      return next;
    } catch (error) {
      setSnapshot((previous) => ({ key, data: previous.key === key ? previous.data : null, error: (error as Error).message }));
      return null;
    }
  }, [workspaceId, path, key]);
  useEffect(() => {
    let mounted = true;
    backendJson<T>(workspaceId, path)
      .then((data) => {
        if (mounted) setSnapshot({ key, data, error: null });
      })
      .catch((error) => {
        if (mounted) setSnapshot({ key, data: null, error: error.message });
      });
    return () => {
      mounted = false;
    };
  }, [workspaceId, path, key]);
  return { data: snapshot.key === key ? snapshot.data : null, error: snapshot.key === key ? snapshot.error : null, reload };
}

export function JobProgress({ workspaceId, id, onChange }: { workspaceId: string; id: string | null | undefined; onChange?: () => void }) {
  const [job, setJob] = useState<S['JobRecord'] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!id) return;
    let active = true,
      current = id,
      failures = 0;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await backendJson<S['JobRecord']>(workspaceId, `/jobs/${current}`);
        if (!active) return;
        failures = 0;
        setError(null);
        setJob(next);
        onChange?.();
        if (next.status === 'done' && next.next_job_id) {
          current = next.next_job_id;
          timer = setTimeout(poll, 0);
        } else if (['queued', 'running'].includes(next.status)) timer = setTimeout(poll, JOB_POLL_INTERVAL_MS);
      } catch (e) {
        if (!active) return;
        // A dropped request (a restart, a flaky connection) must not end the poll: retry with backoff,
        // and only show an error once several requests in a row have failed.
        failures += 1;
        if (shouldShowPollError(failures)) setError(`Progress could not be checked: ${(e as Error).message} Retrying.`);
        timer = setTimeout(poll, jobRetryDelay(failures));
      }
    }
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [id, workspaceId, onChange]);
  if (!id) return null;
  // A finished chain needs no progress bar; failures and running jobs do.
  if (job?.status === 'done' && !job.next_job_id && !error) return null;
  const percent = job?.total ? Math.round((job.done / job.total) * 100) : null;
  return (
    <div className="my-3 rounded-md border border-line bg-soft px-3 py-2 text-xs" role="status">
      <ErrorNote error={error ?? (job?.status === 'failed' ? (job.error ?? 'This step failed.') : null)} />
      {job ? (
        <>
          <div className="flex justify-between gap-3">
            <span className="font-medium">{label(job.kind)}</span>
            <span className="text-muted">
              {job.status === 'queued' ? 'Waiting to start' : job.total ? `${job.done} of ${job.total}` : label(job.status)}
            </span>
          </div>
          <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-line" aria-hidden>
            <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${percent ?? 5}%` }} />
          </div>
        </>
      ) : (
        'Checking progress…'
      )}
    </div>
  );
}

const TENDER_DOC_KINDS = ['question_pack', 'specification', 'clarification_log', 'contract_terms', 'other'] as const;

/** A file picker with a drop zone that shows the chosen file. The input stays in the DOM for forms and tests. */
export function FilePicker({
  name = 'file',
  file,
  onFile,
  accept = '.docx,.xlsx,.pdf',
  hint = 'Word, Excel or PDF',
  inputLabel = 'Choose document',
}: {
  name?: string;
  file: File | null;
  onFile: (file: File | null) => void;
  accept?: string;
  hint?: string;
  inputLabel?: string;
}) {
  const [over, setOver] = useState(false);
  return (
    <label
      className={`flex min-h-20 cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border-2 border-dashed px-4 py-4 text-center text-xs transition-colors ${over ? 'border-accent bg-tint' : 'border-line bg-soft hover:border-line-strong'}`}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const dropped = e.dataTransfer.files[0];
        if (dropped) {
          const input = e.currentTarget.querySelector('input');
          if (input) input.files = e.dataTransfer.files;
          onFile(dropped);
        }
      }}
    >
      <input
        className="sr-only"
        aria-label={inputLabel}
        type="file"
        name={name}
        accept={accept}
        onChange={(e) => onFile(e.target.files?.[0] ?? null)}
      />
      {file ? (
        <>
          <span className="font-medium text-ink wrap-anywhere">{file.name}</span>
          <span className="text-muted">{Math.max(1, Math.round(file.size / 1024))} KB · click to choose a different file</span>
        </>
      ) : (
        <>
          <span className="font-medium text-ink">Drop a file here or click to choose</span>
          <span className="text-muted">{hint}</span>
        </>
      )}
    </label>
  );
}

export function Upload({
  workspaceId,
  tenderId,
  onUploaded,
  defaultKind = 'question_pack',
  kinds = TENDER_DOC_KINDS,
}: {
  workspaceId: string;
  tenderId?: string;
  onUploaded?: () => void;
  defaultKind?: (typeof TENDER_DOC_KINDS)[number];
  kinds?: readonly (typeof TENDER_DOC_KINDS)[number][];
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<string | null>(null),
    [file, setFile] = useState<File | null>(null),
    [kind, setKind] = useState<string>(defaultKind),
    [picker, setPicker] = useState(0);
  const [document, setDocument] = useState<S['DocumentWithJob'] | null>(null);
  async function upload() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const body = new FormData();
      body.set('file', file);
      if (tenderId) body.set('tender_doc_kind', kind);
      const response = await backendRequest(workspaceId, tenderId ? `/tenders/${tenderId}/documents` : '/documents', 'POST', body);
      setDocument(await response.json());
      setFile(null);
      setPicker((n) => n + 1);
      onUploaded?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-3">
      <FilePicker key={picker} file={file} onFile={setFile} />
      <div className="flex flex-wrap items-end gap-3">
        {tenderId && kinds.length > 1 ? (
          <div className="min-w-48 flex-1">
            <Field title="Document kind">
              <select className={field} value={kind} onChange={(e) => setKind(e.target.value)}>
                {kinds.map((v) => (
                  <option key={v} value={v}>
                    {label(v)}
                  </option>
                ))}
              </select>
            </Field>
          </div>
        ) : null}
        <Button variant="primary" busy={busy} disabled={!file} onClick={() => void upload()}>
          Upload
        </Button>
      </div>
      <ErrorNote error={error} />
      <JobProgress workspaceId={workspaceId} id={document?.job_id} onChange={onUploaded} />
      {document && tenderId ? (
        <p className="text-xs text-muted">
          {document.filename} uploaded. {kind === 'question_pack' ? 'Questions appear as they are found.' : 'It is being read now.'}
        </p>
      ) : null}
      {document && !tenderId ? <UploadedDocument key={document.id} workspaceId={workspaceId} uploaded={document} onChange={onUploaded} /> : null}
    </div>
  );
}

export const DOC_KINDS = [
  'dspt_confirmation',
  'cyber_essentials_plus',
  'iso_27001',
  'clinical_safety_case',
  'information_security_policy',
  'product_description',
  'other',
] as const;

const fmtDate = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }) : null;

/** Whether a library document has a classification proposal waiting for a person. */
export function needsConfirmation(d: Pick<S['DocumentWithJob'], 'doc_type' | 'classification_confirmed'>): boolean {
  return Boolean(d.doc_type) && d.doc_type !== 'tender_document' && !d.classification_confirmed;
}

/**
 * Correct a library document's classification. `PATCH /documents/{id}` confirms it at the same time; a
 * changed date is recorded as set by a person, and a changed type re-runs extraction on the worker.
 */
export function ClassificationForm({
  workspaceId,
  document,
  onSaved,
  onCancel,
  submitLabel = 'Save classification',
}: {
  workspaceId: string;
  document: S['DocumentWithJob'];
  onSaved: () => void;
  onCancel?: () => void;
  submitLabel?: string;
}) {
  const [docType, setDocType] = useState(document.doc_type ?? 'reference'),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<string | null>(null);
  return (
    <form
      className="grid gap-3 sm:grid-cols-2"
      onSubmit={async (e) => {
        e.preventDefault();
        const f = new FormData(e.currentTarget);
        const body: Record<string, unknown> = {
          doc_type: docType,
          doc_kind: docType === 'reference' ? f.get('doc_kind') || null : null,
        };
        if (f.get('effective_date')) body.effective_date = f.get('effective_date');
        if (docType === 'past_submission') {
          body.buyer = f.get('buyer') || null;
          body.submission_date = f.get('submission_date') || null;
        }
        setBusy(true);
        setError(null);
        try {
          await backendJson(workspaceId, `/documents/${document.id}`, 'PATCH', body);
          onSaved();
        } catch (err) {
          setError((err as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <Field title="Document type">
        <select className={field} name="doc_type" value={docType} onChange={(e) => setDocType(e.target.value)}>
          <option value="past_submission">{label('past_submission')}</option>
          <option value="reference">{label('reference')}</option>
        </select>
      </Field>
      {docType === 'reference' ? (
        <Field title="Reference kind">
          <select className={field} name="doc_kind" required defaultValue={document.doc_kind ?? ''}>
            <option value="" disabled>
              Choose a kind
            </option>
            {DOC_KINDS.map((v) => (
              <option key={v} value={v}>
                {label(v)}
              </option>
            ))}
          </select>
        </Field>
      ) : (
        <Field title="Buyer">
          <input className={field} name="buyer" defaultValue={document.buyer ?? ''} />
        </Field>
      )}
      <Field title="Effective date">
        <input className={field} name="effective_date" type="date" required defaultValue={document.effective_date ?? ''} />
      </Field>
      {docType === 'past_submission' ? (
        <Field title="Submission date">
          <input className={field} name="submission_date" type="date" defaultValue={document.submission_date ?? ''} />
        </Field>
      ) : null}
      <div className="flex flex-wrap items-end gap-2 sm:col-span-2">
        <Button type="submit" variant="primary" busy={busy}>
          {submitLabel}
        </Button>
        {onCancel ? (
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        ) : null}
      </div>
      <div className="sm:col-span-2">
        <ErrorNote error={error} />
      </div>
    </form>
  );
}

/**
 * The proposal the classifier made for a library document, with the two ways to settle it: confirm as
 * proposed (`POST /documents/{id}/confirm`) or correct it, which also confirms. Supersession between
 * documents is evaluated only once both are confirmed, so this prompt stays until someone acts.
 */
export function ClassificationPrompt({
  workspaceId,
  document,
  onChange,
  showOpenLink = false,
}: {
  workspaceId: string;
  document: S['DocumentWithJob'];
  onChange: () => void;
  showOpenLink?: boolean;
}) {
  const [editing, setEditing] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<string | null>(null);
  const date = fmtDate(document.effective_date);
  const proposal = [
    label(document.doc_type),
    document.doc_type === 'reference' && document.doc_kind ? label(document.doc_kind) : null,
    date
      ? `effective ${date}${document.effective_date_source === 'upload_time' ? ' (no date found in the document; the upload date was used)' : ''}`
      : 'no effective date',
    document.doc_type === 'past_submission' && document.buyer ? `buyer ${document.buyer}` : null,
    document.doc_type === 'past_submission' && document.submission_date ? `submitted ${fmtDate(document.submission_date)}` : null,
  ].filter(Boolean);
  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await backendJson(workspaceId, `/documents/${document.id}/confirm`, 'POST', {});
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-2 rounded-md border border-amber/40 bg-amber-bg/40 px-3 py-2 text-sm" role="group" aria-label={`Confirm the classification of ${document.filename}`}>
      <p>
        <span className="font-semibold">Check the proposed classification of {document.filename}:</span> {proposal.join(' · ')}.
      </p>
      {editing ? (
        <ClassificationForm
          workspaceId={workspaceId}
          document={document}
          submitLabel="Save and confirm"
          onCancel={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            onChange();
          }}
        />
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="primary" busy={busy} onClick={() => void confirm()}>
            Confirm
          </Button>
          <Button size="sm" onClick={() => setEditing(true)}>
            Correct it
          </Button>
          {showOpenLink ? (
            <Link className="text-xs text-accent underline" href={`/w/${workspaceId}/library/${document.id}`}>
              Open document
            </Link>
          ) : null}
        </div>
      )}
      <ErrorNote error={error} />
    </div>
  );
}

/**
 * After a library upload: poll the document every two seconds until the classifier has proposed a type
 * (or ingestion failed), then show the confirmation prompt where the upload was started.
 */
function UploadedDocument({ workspaceId, uploaded, onChange }: { workspaceId: string; uploaded: S['DocumentWithJob']; onChange?: () => void }) {
  const [doc, setDoc] = useState<S['DocumentWithJob']>(uploaded),
    [failures, setFailures] = useState(0);
  const settled = Boolean(doc.doc_type) || doc.ingest_status === 'failed';
  const reload = useCallback(async () => {
    try {
      setDoc(await backendJson<S['DocumentWithJob']>(workspaceId, `/documents/${uploaded.id}`));
      setFailures(0);
    } catch {
      setFailures((n) => n + 1);
    }
  }, [workspaceId, uploaded.id]);
  useEffect(() => {
    if (settled) return;
    const timer = setTimeout(() => void reload(), failures ? jobRetryDelay(failures) : JOB_POLL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [settled, reload, failures, doc]);
  if (doc.ingest_status === 'failed') return null; // JobProgress shows the error.
  if (!doc.doc_type) return <p className="text-xs text-muted">{doc.filename} uploaded. Its proposed classification appears here once it has been read.</p>;
  if (needsConfirmation(doc))
    return (
      <ClassificationPrompt
        workspaceId={workspaceId}
        document={doc}
        showOpenLink
        onChange={() => {
          void reload();
          onChange?.();
        }}
      />
    );
  return (
    <p className="text-xs text-muted">
      {doc.filename}: classification confirmed as {label(doc.doc_type)}
      {doc.doc_type === 'reference' && doc.doc_kind ? ` · ${label(doc.doc_kind)}` : ''}.
    </p>
  );
}
