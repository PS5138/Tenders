'use client';
import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { backendJson, backendRequest } from '@/lib/backend-api';
import type { components } from '@/server/backend/schema';
import { Button } from '@/components/ui/button';
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
      current = id;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await backendJson<S['JobRecord']>(workspaceId, `/jobs/${current}`);
        if (!active) return;
        setJob(next);
        onChange?.();
        if (next.status === 'done' && next.next_job_id) {
          current = next.next_job_id;
          timer = setTimeout(poll, 0);
        } else if (['queued', 'running'].includes(next.status)) timer = setTimeout(poll, 2000);
      } catch (e) {
        if (active) setError((e as Error).message);
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
      {document ? (
        <p className="text-xs text-muted">
          {document.filename} uploaded.{' '}
          {tenderId ? (kind === 'question_pack' ? 'Questions appear as they are found.' : 'It is being read now.') : 'Confirm its proposed classification below.'}
        </p>
      ) : null}
    </div>
  );
}
