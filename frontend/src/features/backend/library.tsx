'use client';
import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { backendJson, backendUrl } from '@/lib/backend-api';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { PageHeader } from '@/components/ui/page-header';
import { ClassificationForm, ClassificationPrompt, ErrorNote, Field, Upload, field, label, needsConfirmation, panel, useResource, type S } from './shared';
import { SourcePane, type Source } from './trace';

export function Library({ workspaceId }: { workspaceId: string }) {
  const documents = useResource<S['DocumentWithJob'][]>(workspaceId, '/documents');
  const decisions = useResource<S['DecisionRecord'][]>(workspaceId, '/library/supersession-decisions');
  const [error, setError] = useState<string | null>(null);
  const { reload: reloadDocuments } = documents;
  const { reload: reloadDecisions } = decisions;
  const refresh = useCallback(() => {
    void reloadDocuments();
    void reloadDecisions();
  }, [reloadDocuments, reloadDecisions]);
  useEffect(() => {
    if (!documents.data?.some((d) => !['ready', 'failed'].includes(d.ingest_status))) return;
    const timer = setInterval(refresh, 2000);
    return () => clearInterval(timer);
  }, [documents.data, refresh]);
  async function decide(id: string, decision: string, superseding_document_id?: string) {
    try {
      await backendJson(workspaceId, `/library/supersession-decisions/${id}`, 'POST', { decision, superseding_document_id });
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <>
      <PageHeader title="Evidence library" subtitle="Reusable submissions and current reference documents for your business." />
      <div className={`${panel} space-y-2`}>
        <h2 className="text-sm font-semibold">Add to the library</h2>
        <p className="text-xs text-muted">Past submissions and current reference documents such as certificates and policies.</p>
        <Upload workspaceId={workspaceId} onUploaded={refresh} />
      </div>
      <ErrorNote error={documents.error ?? decisions.error ?? error} />
      <div className="mt-5 space-y-3">
        {documents.data?.map((d) => {
          const counts = Object.entries(d.item_counts ?? {}).filter(([, count]) => count > 0);
          return (
            <article className={panel} key={d.id}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <Link className="font-semibold text-accent wrap-anywhere" href={`/w/${workspaceId}/library/${d.id}`}>
                  {d.filename}
                </Link>
                <span className="flex flex-wrap gap-1.5">
                  {d.superseded_by ? <Badge tone="amber">Superseded</Badge> : null}
                  <Badge tone={d.ingest_status === 'ready' ? 'green' : d.ingest_status === 'failed' ? 'red' : 'blue'}>
                    {label(d.ingest_status)}
                  </Badge>
                </span>
              </div>
              <p className="mt-1 text-xs text-muted">
                {d.doc_type ? label(d.doc_type) : 'Awaiting classification'}
                {d.doc_kind && d.doc_kind !== 'other' ? ` · ${label(d.doc_kind)}` : ''} ·{' '}
                {d.effective_date ? `effective ${new Date(d.effective_date).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}` : 'date pending'}
              </p>
              {d.ingest_status === 'ready' ? (
                <p className="mt-2 text-xs">
                  {[...counts.map(([kind, count]) => `${count} ${label(kind)}`), `${d.fact_count ?? 0} dated facts`].join(' · ')}
                </p>
              ) : null}
              {d.superseded_by ? <p className="mt-2 text-xs text-amber">A newer document of the same kind replaces this one when drafting.</p> : null}
              {needsConfirmation(d) ? (
                <div className="mt-2">
                  <ClassificationPrompt workspaceId={workspaceId} document={d} onChange={refresh} showOpenLink />
                </div>
              ) : null}
              <ErrorNote error={d.ingest_error} />
            </article>
          );
        })}
      </div>
      {documents.data?.length === 0 ? (
        <p className="mt-5 text-sm text-muted">Upload a past submission or reference document to begin.</p>
      ) : null}
      {(decisions.data ?? []).length ? (
        <section className="mt-6">
          <h2 className="mb-3 font-semibold">Supersession decisions</h2>
          {decisions.data!.map((d) => (
            <div key={d.id} className={`${panel} mb-3`}>
              <p className="text-sm">
                {d.document_a.filename} / {d.document_b.filename}
              </p>
              <p className="my-2 text-xs text-muted">
                {label(d.reason)} · {d.document_a.effective_date ?? 'Undated'} / {d.document_b.effective_date ?? 'Undated'}
              </p>
              <div className="flex flex-wrap gap-2">
                <Button onClick={() => void decide(d.id, 'keep_both')}>Keep both</Button>
                <Button onClick={() => void decide(d.id, 'superseded', d.document_a.id)}>Use {d.document_a.filename}</Button>
                <Button onClick={() => void decide(d.id, 'superseded', d.document_b.id)}>Use {d.document_b.filename}</Button>
              </div>
            </div>
          ))}
        </section>
      ) : null}
    </>
  );
}
export function LibraryDocument({ workspaceId, documentId }: { workspaceId: string; documentId: string }) {
  const doc = useResource<S['DocumentWithJob']>(workspaceId, `/documents/${documentId}`);
  const sections = useResource<S['SectionRecord'][]>(workspaceId, `/documents/${documentId}/sections`);
  const queue = useResource<S['UnpairedResponse']>(workspaceId, `/documents/${documentId}/unpaired`);
  const [source, setSource] = useState<Source | null>(null),
    [error, setError] = useState<string | null>(null),
    [busy, setBusy] = useState(false);
  const { reload: reloadDoc } = doc;
  const { reload: reloadQueue } = queue;
  const { reload: reloadSections } = sections;
  const refresh = useCallback(() => {
    void reloadDoc();
    void reloadSections();
    void reloadQueue();
  }, [reloadDoc, reloadSections, reloadQueue]);
  useEffect(() => {
    if (!doc.data || ['ready', 'failed'].includes(doc.data.ingest_status)) return;
    const timer = setInterval(refresh, 2000);
    return () => clearInterval(timer);
  }, [doc.data, refresh]);
  async function change(path: string, method: string, body: unknown) {
    setBusy(true);
    setError(null);
    try {
      await backendJson(workspaceId, path, method, body);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const d = doc.data;
  return (
    <>
      <Link className="text-xs text-accent" href={`/w/${workspaceId}/library`}>
        ← Evidence library
      </Link>
      <PageHeader
        title={d?.filename ?? 'Document'}
        subtitle={d ? `${label(d.ingest_status)} · ${d.effective_date ?? 'Date pending'}` : 'Loading…'}
      />
      <ErrorNote error={doc.error ?? sections.error ?? queue.error ?? error ?? d?.ingest_error} />
      {d ? (
        <div className="mb-5 flex flex-wrap gap-2">
          <a className="text-sm text-accent underline" href={backendUrl(workspaceId, `/documents/${documentId}/file`)}>
            Download original
          </a>
        </div>
      ) : null}
      {d && needsConfirmation(d) ? (
        <div className="mb-5">
          <ClassificationPrompt workspaceId={workspaceId} document={d} onChange={refresh} />
        </div>
      ) : d && d.doc_type !== 'tender_document' ? (
        <section className={`${panel} mb-5 space-y-3`}>
          <h2 className="text-sm font-semibold">Classification</h2>
          <ClassificationForm key={`${d.id}-${d.doc_type}-${d.doc_kind}-${d.effective_date}`} workspaceId={workspaceId} document={d} onSaved={refresh} />
        </section>
      ) : null}
      <div className={`grid gap-4 ${source ? 'xl:grid-cols-2' : ''}`}>
        <div>
          <h2 className="mb-3 font-semibold">Stored sections</h2>
          {sections.data?.map((s) => (
            <button
              className={`${panel} mb-2 block w-full text-left`}
              key={s.id}
              onClick={() =>
                setSource({
                  source_type: 'knowledge_item',
                  source_id: s.id,
                  quote: '',
                  document_title: d?.filename ?? '',
                  locator: {
                    document_id: documentId,
                    section_id: s.id,
                    start: null,
                    end: null,
                  },
                })
              }
            >
              <span className="text-xs text-muted">{s.cell_ref ?? s.heading_path.join(' / ')}</span>
              <pre className="mt-2 max-h-28 overflow-hidden whitespace-pre-wrap break-words font-sans text-sm">{s.text}</pre>
            </button>
          ))}
        </div>
        {source ? (
          <SourcePane key={JSON.stringify(source.locator)} workspaceId={workspaceId} source={source} onClose={() => setSource(null)} />
        ) : null}
      </div>
      <section className={`${panel} mt-6`}>
        <h2 className="font-semibold">Extraction fixes</h2>
        <p className="my-2 text-xs text-muted">
          {queue.data?.items.length ?? 0} unverified items · {queue.data?.fragments.length ?? 0} unpaired fragments. Confirm a span against
          the stored section; offsets count Unicode characters.
        </p>
        {(queue.data?.items ?? []).map((item) => (
          <p key={item.id} className="my-2 text-sm">
            Unverified: {item.question_text} — {item.answer_text}
          </p>
        ))}
        {(queue.data?.fragments ?? []).map((fragment) => (
          <p key={fragment.id} className="my-2 text-sm">
            {fragment.role}: {fragment.text}
          </p>
        ))}
        <form
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            void change(`/documents/${documentId}/pairs`, 'POST', {
              section_id: f.get('section_id'),
              answer_start: Number(f.get('start')),
              answer_end: Number(f.get('end')),
              question_text: f.get('question') || undefined,
              item_id: f.get('item') || undefined,
              question_fragment_id: f.get('fragment') || undefined,
            });
          }}
        >
          <Field title="Answer section">
            <select required className={field} name="section_id">
              {sections.data?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.order_index + 1}: {s.cell_ref ?? s.heading_path.join(' / ')}
                </option>
              ))}
            </select>
          </Field>
          <Field title="Question text">
            <input className={field} name="question" />
          </Field>
          <Field title="Start offset (inclusive)">
            <input className={field} name="start" type="number" min="0" required />
          </Field>
          <Field title="End offset (exclusive)">
            <input className={field} name="end" type="number" min="1" required />
          </Field>
          <Field title="Fix an unverified item">
            <select className={field} name="item">
              <option value="">New pair</option>
              {queue.data?.items.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.question_text ?? i.id}
                </option>
              ))}
            </select>
          </Field>
          <Field title="Pair a fragment">
            <select className={field} name="fragment">
              <option value="">No fragment</option>
              {queue.data?.fragments.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.text.slice(0, 70)}
                </option>
              ))}
            </select>
          </Field>
          <Button busy={busy} type="submit">
            Confirm pair at these offsets
          </Button>
        </form>
      </section>
    </>
  );
}
