'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api-client';
import { backendJson } from '@/lib/backend-api';
import { Button } from '@/components/ui/button';
import { PageHeader } from '@/components/ui/page-header';
import type { FormLocation } from '@/server/backend/forms';
import { ErrorNote, Field, field, panel, useResource, type S } from './shared';

export function BuyerForms({ workspaceId, tenderId }: { workspaceId: string; tenderId: string }) {
  const tender = useResource<S['TenderDetail']>(workspaceId, `/tenders/${tenderId}`);
  const questions = useResource<S['QuestionListItem'][]>(workspaceId, `/tenders/${tenderId}/questions`);
  const [locations, setLocations] = useState<FormLocation[]>([]),
    [error, setError] = useState<string | null>(null),
    [busy, setBusy] = useState(false),
    [documentId, setDocumentId] = useState('');
  const [sections, setSections] = useState<S['SectionRecord'][]>([]);
  useEffect(() => {
    let active = true;
    if (documentId)
      void backendJson<S['SectionRecord'][]>(workspaceId, `/documents/${documentId}/sections`)
        .then((rows) => {
          if (active) setSections(rows);
        })
        .catch((error) => {
          if (active) setError(error.message);
        });
    return () => {
      active = false;
    };
  }, [documentId, workspaceId]);
  const endpoint = `/api/w/${workspaceId}/tenders/${tenderId}/forms`;
  useEffect(() => {
    let active = true;
    api<FormLocation[]>(endpoint, { method: 'GET' })
      .then((rows) => {
        if (active) setLocations(rows);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [endpoint]);
  const documents = (tender.data?.documents ?? []).filter((d) => /\.(docx|xlsx)$/i.test(d.filename));
  const selected = documents.find((d) => d.id === documentId),
    excel = selected?.filename.toLowerCase().endsWith('.xlsx');
  async function download() {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`${endpoint}/export`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-ten-request': '1' },
        body: JSON.stringify({ documentId }),
      });
      if (!response.ok) {
        const result = await response.json();
        throw new Error(result.detail ?? result.error?.message ?? 'Export failed.');
      }
      const url = URL.createObjectURL(await response.blob());
      const a = document.createElement('a');
      a.href = url;
      a.download = `completed-${selected?.filename}`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Link className="text-sm text-accent" href={`/w/${workspaceId}/tenders/${tenderId}`}>
        ← Tender workspace
      </Link>
      <PageHeader
        title="Fill the buyer’s form"
        subtitle="Confirm each answer location against the original file. Only approved, current answers are exported; existing text and formulas are protected."
      />
      <ErrorNote error={error ?? tender.error ?? questions.error} />
      <div className={`${panel} space-y-4`}>
        <Field title="Original Word or Excel form">
          <select className={field} value={documentId} onChange={(e) => setDocumentId(e.target.value)}>
            <option value="">Choose a form</option>
            {documents.map((d) => (
              <option key={d.id} value={d.id}>
                {d.filename}
              </option>
            ))}
          </select>
        </Field>
        {selected ? (
          <>
            <a className="text-sm text-accent underline" href={`/api/w/${workspaceId}/backend/documents/${documentId}/file`}>
              Download original to check locations
            </a>
            <p className="text-xs text-muted">
              Excel uses the exact worksheet name and cell reference. Word table, row and cell numbers start at 1. Extraction references
              identify the question; confirm the empty answer cell yourself.
            </p>
            {questions.data?.map((q) => {
              const saved = locations.find((l) => l.questionId === q.id && l.documentId === documentId);
              return (
                <form
                  key={`${q.id}-${documentId}-${saved?.id ?? ''}`}
                  className="space-y-3 border-t border-line pt-4"
                  onSubmit={async (e) => {
                    e.preventDefault();
                    const f = new FormData(e.currentTarget);
                    setBusy(true);
                    setError(null);
                    try {
                      const target = excel
                        ? {
                            kind: 'xlsx_cell',
                            sheet: f.get('sheet'),
                            ref: String(f.get('ref')).toUpperCase(),
                          }
                        : {
                            kind: 'docx_table_cell',
                            table: Number(f.get('table')),
                            row: Number(f.get('row')),
                            cell: Number(f.get('cell')),
                          };
                      await api(endpoint, {
                        body: { questionId: q.id, documentId, target },
                      });
                      setLocations(await api<FormLocation[]>(endpoint, { method: 'GET' }));
                    } catch (e) {
                      setError((e as Error).message);
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  <p className="text-sm font-semibold">
                    {q.number} · {q.text}
                  </p>
                  <p className="text-xs text-muted">
                    {q.status}
                    {saved ? ' · Location confirmed' : ''}
                  </p>
                  {q.document_id === documentId
                    ? sections
                        .filter((section) => section.document_id === documentId && section.text.includes(q.text))
                        .map((section) => (
                          <p key={section.id} className="text-xs text-muted">
                            Extracted question reference: {section.cell_ref ?? section.heading_path.join(' / ')}. Confirm the answer cell
                            separately.
                          </p>
                        ))
                    : null}
                  <div className="grid items-end gap-3 sm:grid-cols-4">
                    {excel ? (
                      <>
                        <Field title="Worksheet">
                          <input
                            name="sheet"
                            className={field}
                            required
                            defaultValue={saved?.target.kind === 'xlsx_cell' ? saved.target.sheet : ''}
                          />
                        </Field>
                        <Field title="Answer cell">
                          <input
                            name="ref"
                            className={field}
                            required
                            placeholder="D12"
                            defaultValue={saved?.target.kind === 'xlsx_cell' ? saved.target.ref : ''}
                          />
                        </Field>
                      </>
                    ) : (
                      (['table', 'row', 'cell'] as const).map((name) => (
                        <Field key={name} title={name}>
                          <input
                            name={name}
                            className={field}
                            type="number"
                            min={1}
                            required
                            defaultValue={saved?.target.kind === 'docx_table_cell' ? saved.target[name] : undefined}
                          />
                        </Field>
                      ))
                    )}
                    <Button busy={busy} type="submit">
                      Confirm location
                    </Button>
                  </div>
                </form>
              );
            })}
            <Button
              variant="primary"
              busy={busy}
              disabled={!locations.some((l) => l.documentId === documentId)}
              onClick={() => void download()}
            >
              Download completed form
            </Button>
          </>
        ) : null}
      </div>
    </>
  );
}
