'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import * as Dropdown from '@radix-ui/react-dropdown-menu';
import { Archive, ArchiveRestore, ChevronDown, Pencil, Plus, Send, Trash2 } from 'lucide-react';
import { api } from '@/lib/api-client';
import { BackendRequestError, backendJson, backendRequest } from '@/lib/backend-api';
import { Badge } from '@/components/ui/badge';
import { Button, buttonClasses } from '@/components/ui/button';
import { PageHeader } from '@/components/ui/page-header';
import { Dialog } from '@/components/ui/dialog';
import { EmptyState } from '@/components/ui/states';
import { StatusBadge } from './badges';
import { QuestionBoard, QuestionList, groupQuestions, type GroupBy, type Reviewer } from './question-views';

const menuItem =
  'flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-[13px] outline-none data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50 data-[highlighted]:bg-soft [&_svg]:size-4';
import { ErrorNote, Field, FilePicker, JobProgress, Upload, field, label, panel, useResource, type S } from './shared';
import { Trace, SourcePane, type Source } from './trace';

const statuses = ['not_started', 'ai_draft', 'writer_edited', 'sme_verified', 'approved'];
const coverages = ['covered', 'partial', 'new', 'unknown'];
const OTHER_KINDS = ['specification', 'clarification_log', 'contract_terms', 'other'] as const;
const questionLink = (ws: string, tender: string, q: string) => `/w/${ws}/tenders/${tender}/responses/${q}`;

function formatDeadline(deadline: string | null | undefined) {
  return deadline
    ? new Date(deadline).toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
    : null;
}

function DeadlineBadge({ days, submitted }: { days: number | null | undefined; submitted: boolean }) {
  if (submitted || days == null) return null;
  if (days < 0) return <Badge tone="red">Overdue by {-days} {-days === 1 ? 'day' : 'days'}</Badge>;
  if (days === 0) return <Badge tone="red">Due today</Badge>;
  return <Badge tone={days <= 7 ? 'amber' : 'neutral'}>{`Due in ${days} ${days === 1 ? 'day' : 'days'}`}</Badge>;
}

function Progress({ done, total, labelText }: { done: number; total: number; labelText: string }) {
  const percent = total ? Math.round((done / total) * 100) : 0;
  return (
    <div className="min-w-40 flex-1">
      <div className="flex justify-between text-xs">
        <span>{labelText}</span>
        <span className="text-muted">
          {done} of {total}
        </span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-line" aria-hidden>
        <div className="h-full rounded-full bg-accent" style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

export function Tenders({ workspaceId }: { workspaceId: string }) {
  const { data, error } = useResource<S['TenderListItem'][]>(workspaceId, '/tenders');
  const [tab, setTab] = useState<'open' | 'submitted' | 'archived'>('open');
  const count = (status: string) => (data ?? []).filter((t) => t.status === status).length;
  const sorted = [...(data ?? [])].filter((t) => t.status === tab).sort((a, b) => {
    // Open tenders first, soonest deadline first; undated ones after dated ones.
    if ((a.status === 'open') !== (b.status === 'open')) return a.status === 'open' ? -1 : 1;
    return (a.days_remaining ?? Infinity) - (b.days_remaining ?? Infinity);
  });
  return (
    <>
      <PageHeader
        title="All tenders"
        subtitle="Keep every submission moving, together."
        actions={
          <Link className={buttonClasses('primary')} href={`/w/${workspaceId}/tenders/new`}>
            <Plus aria-hidden /> New tender
          </Link>
        }
      />
      <ErrorNote error={error} />
      <div role="tablist" aria-label="Tender status" className="mb-4 flex w-fit gap-1 rounded-lg bg-soft p-1">
        {(
          [
            ['open', 'Active'],
            ['submitted', 'Submitted'],
            ['archived', 'Archived'],
          ] as const
        ).map(([key, name]) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            className={`rounded-md px-3 py-1.5 text-[13px] ${tab === key ? 'bg-bg font-semibold shadow-sm' : 'text-muted hover:text-ink'}`}
            onClick={() => setTab(key)}
          >
            {name} <span className="text-muted">{count(key)}</span>
          </button>
        ))}
      </div>
      <div className="space-y-3">
        {sorted.map((t) => (
          <Link key={t.id} href={`/w/${workspaceId}/tenders/${t.id}`} className={`${panel} block hover:bg-soft`}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 className="font-semibold wrap-anywhere">{t.name}</h2>
                <p className="text-xs text-muted">
                  {t.buyer ?? 'Buyer not set'}
                  {t.deadline ? ` · Deadline ${formatDeadline(t.deadline)}` : ' · No deadline set'}
                </p>
              </div>
              <div className="flex flex-wrap gap-1.5">
                <DeadlineBadge days={t.days_remaining} submitted={t.status !== 'open'} />
                <Badge tone={t.status === 'open' ? 'blue' : 'neutral'}>{label(t.status)}</Badge>
                {t.outcome && !['pending', 'unknown'].includes(t.outcome) ? (
                  <Badge tone={t.outcome === 'won' ? 'green' : 'red'}>{label(t.outcome)}</Badge>
                ) : null}
              </div>
            </div>
            {t.questions_total ? (
              <div className="mt-3 flex flex-wrap gap-6">
                <Progress done={t.questions_approved} total={t.questions_total} labelText="Questions approved" />
                {t.words_total ? <Progress done={t.words_approved} total={t.words_total} labelText="Words approved" /> : null}
              </div>
            ) : (
              <p className="mt-3 text-xs text-amber">No questions yet. Open the tender to upload the question pack.</p>
            )}
          </Link>
        ))}
      </div>
      {data?.length && !sorted.length ? (
        <p className="text-sm text-muted">{tab === 'archived' ? 'No archived tenders.' : tab === 'submitted' ? 'No submitted tenders yet.' : 'No active tenders.'}</p>
      ) : null}
      {data?.length === 0 ? (
        <EmptyState
          title="No tenders yet"
          action={
            <Link className={buttonClasses('primary')} href={`/w/${workspaceId}/tenders/new`}>
              New tender
            </Link>
          }
        >
          Create a tender and upload the buyer’s question pack. Ten finds every question and checks what your library already covers.
        </EmptyState>
      ) : null}
    </>
  );
}

type Extra = { key: number; file: File | null; kind: (typeof OTHER_KINDS)[number]; uploaded?: boolean };

/** Details, then the buyer's documents, in one flow. The tender is created only when the files are sent. */
export function NewTender({ workspaceId }: { workspaceId: string }) {
  const router = useRouter();
  const [step, setStep] = useState<1 | 2>(1),
    [details, setDetails] = useState({ name: '', buyer: '', deadline: '', regime: '' }),
    [pack, setPack] = useState<File | null>(null),
    [packUploaded, setPackUploaded] = useState(false),
    [extras, setExtras] = useState<Extra[]>([]),
    [tenderId, setTenderId] = useState<string | null>(null),
    [progress, setProgress] = useState<string | null>(null),
    [error, setError] = useState<string | null>(null),
    [busy, setBusy] = useState(false);
  async function submit() {
    if (!pack) return;
    setBusy(true);
    setError(null);
    try {
      let id = tenderId;
      if (!id) {
        setProgress('Creating the tender…');
        const created = await backendJson<S['TenderDetail']>(workspaceId, '/tenders', 'POST', {
          name: details.name,
          buyer: details.buyer || null,
          deadline: details.deadline ? new Date(details.deadline).toISOString() : null,
          regime: details.regime || null,
        });
        id = created.id;
        setTenderId(id);
      }
      const send = async (file: File, kind: string) => {
        const body = new FormData();
        body.set('file', file);
        body.set('tender_doc_kind', kind);
        await backendRequest(workspaceId, `/tenders/${id}/documents`, 'POST', body);
      };
      if (!packUploaded) {
        setProgress(`Uploading ${pack.name}…`);
        await send(pack, 'question_pack');
        setPackUploaded(true);
      }
      for (const extra of extras) {
        if (!extra.file || extra.uploaded) continue;
        setProgress(`Uploading ${extra.file.name}…`);
        await send(extra.file, extra.kind);
        setExtras((list) => list.map((e) => (e.key === extra.key ? { ...e, uploaded: true } : e)));
      }
      router.push(`/w/${workspaceId}/tenders/${id}`);
    } catch (e) {
      setError(
        `${(e as Error).message}${tenderId || progress !== 'Creating the tender…' ? ' The tender was created; retry to send the remaining files, or open it and upload them there.' : ''}`,
      );
      setProgress(null);
      setBusy(false);
    }
  }
  const steps = ['Tender details', 'Buyer documents'];
  return (
    <>
      <PageHeader title="New tender" subtitle="Add the tender’s details and the buyer’s documents. Ten then finds every question for you." />
      <ol className="mb-5 flex flex-wrap gap-2 text-xs" aria-label="Steps">
        {steps.map((name, i) => (
          <li
            key={name}
            aria-current={step === i + 1 ? 'step' : undefined}
            className={`flex items-center gap-2 rounded-full border px-3 py-1 ${step === i + 1 ? 'border-accent bg-tint font-semibold text-accent' : 'border-line text-muted'}`}
          >
            <span>{i + 1}</span> {name}
          </li>
        ))}
      </ol>
      {step === 1 ? (
        <form
          className={`${panel} max-w-2xl space-y-4`}
          onSubmit={(e) => {
            e.preventDefault();
            setStep(2);
          }}
        >
          <Field title="Tender name">
            <input
              className={field}
              name="name"
              required
              maxLength={512}
              value={details.name}
              onChange={(e) => setDetails({ ...details, name: e.target.value })}
            />
          </Field>
          <Field title="Buyer">
            <input
              className={field}
              name="buyer"
              maxLength={256}
              value={details.buyer}
              onChange={(e) => setDetails({ ...details, buyer: e.target.value })}
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field title="Submission deadline (your local time)">
              <input
                className={field}
                name="deadline"
                type="datetime-local"
                value={details.deadline}
                onChange={(e) => setDetails({ ...details, deadline: e.target.value })}
              />
            </Field>
            <Field title="Procurement regime">
              <select className={field} name="regime" value={details.regime} onChange={(e) => setDetails({ ...details, regime: e.target.value })}>
                <option value="">Not set</option>
                {['procurement_act', 'psr', 'pcr_2015', 'other'].map((x) => (
                  <option key={x} value={x}>
                    {label(x)}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <div className="flex justify-end gap-2">
            <Link className={buttonClasses('ghost')} href={`/w/${workspaceId}/tenders`}>
              Cancel
            </Link>
            <Button type="submit" variant="primary">
              Continue
            </Button>
          </div>
        </form>
      ) : (
        <div className={`${panel} max-w-2xl space-y-5`}>
          <section className="space-y-2">
            <h2 className="text-sm font-semibold">Question pack</h2>
            <p className="text-xs text-muted">
              The buyer’s list of questions, usually an Excel or Word file. Ten extracts every question from it into a tracked card.
            </p>
            {packUploaded ? (
              <p className="text-xs text-accent">{pack?.name} uploaded.</p>
            ) : (
              <FilePicker name="question_pack" file={pack} onFile={setPack} inputLabel="Choose question pack" />
            )}
          </section>
          <section className="space-y-2">
            <h2 className="text-sm font-semibold">Other buyer documents (optional)</h2>
            <p className="text-xs text-muted">
              Specification, clarification log or contract terms. They are kept with the tender for reference and are not used to draft answers.
            </p>
            {extras.map((extra) => (
              <div key={extra.key} className="grid gap-2 rounded-lg border border-line p-3 sm:grid-cols-[1fr_12rem_auto] sm:items-end">
                {extra.uploaded ? (
                  <p className="text-xs text-accent">{extra.file?.name} uploaded.</p>
                ) : (
                  <FilePicker
                    file={extra.file}
                    inputLabel="Choose additional document"
                    onFile={(file) => setExtras((list) => list.map((e) => (e.key === extra.key ? { ...e, file } : e)))}
                  />
                )}
                <Field title="Document kind">
                  <select
                    className={field}
                    value={extra.kind}
                    disabled={extra.uploaded}
                    onChange={(e) =>
                      setExtras((list) =>
                        list.map((x) => (x.key === extra.key ? { ...x, kind: e.target.value as Extra['kind'] } : x)),
                      )
                    }
                  >
                    {OTHER_KINDS.map((k) => (
                      <option key={k} value={k}>
                        {label(k)}
                      </option>
                    ))}
                  </select>
                </Field>
                <Button
                  variant="ghost"
                  aria-label="Remove this document"
                  disabled={extra.uploaded || busy}
                  onClick={() => setExtras((list) => list.filter((e) => e.key !== extra.key))}
                >
                  <Trash2 aria-hidden />
                </Button>
              </div>
            ))}
            <Button
              size="sm"
              disabled={busy}
              onClick={() => setExtras((list) => [...list, { key: Date.now(), file: null, kind: 'specification' }])}
            >
              <Plus aria-hidden /> Add a document
            </Button>
          </section>
          <ErrorNote error={error} />
          {progress ? (
            <p role="status" className="text-xs text-muted">
              {progress}
            </p>
          ) : null}
          <div className="flex flex-wrap justify-end gap-2">
            {tenderId ? (
              <Link className={buttonClasses('ghost')} href={`/w/${workspaceId}/tenders/${tenderId}`}>
                Open the tender
              </Link>
            ) : (
              <Button variant="ghost" disabled={busy} onClick={() => setStep(1)}>
                Back
              </Button>
            )}
            <Button
              variant="primary"
              busy={busy}
              disabled={!pack || extras.some((e) => !e.file && !e.uploaded)}
              onClick={() => void submit()}
            >
              {tenderId ? 'Retry upload' : 'Create tender'}
            </Button>
          </div>
        </div>
      )}
    </>
  );
}

const VIEWS = [
  ['board', 'Board'],
  ['list', 'List'],
  ['document', 'Document'],
  ['documents', 'Buyer documents'],
  ['submission', 'Submission'],
] as const;
const compact = 'rounded-md border border-line bg-bg px-2 py-1.5 text-xs text-ink';
const toLocalInput = (iso: string | null | undefined) => {
  if (!iso) return '';
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

export function TenderWorkspace({
  workspaceId,
  tenderId,
  isAdmin,
  members,
}: {
  workspaceId: string;
  tenderId: string;
  isAdmin: boolean;
  members: { userId: string; displayName: string }[];
}) {
  const router = useRouter();
  const tender = useResource<S['TenderDetail']>(workspaceId, `/tenders/${tenderId}`),
    questions = useResource<S['QuestionListItem'][]>(workspaceId, `/tenders/${tenderId}/questions`);
  const [view, setView] = useState('board'),
    [status, setStatus] = useState(''),
    [coverage, setCoverage] = useState(''),
    [search, setSearch] = useState(''),
    [sort, setSort] = useState('order'),
    [owner, setOwner] = useState(''),
    [groupBy, setGroupBy] = useState<GroupBy>('status'),
    [notice, setNotice] = useState<string | null>(null),
    [editing, setEditing] = useState(false),
    [deleting, setDeleting] = useState(false),
    [confirmName, setConfirmName] = useState(''),
    [error, setError] = useState<string | null>(null),
    [job, setJob] = useState<string | null>(null),
    [busy, setBusy] = useState(false),
    [reviewers, setReviewers] = useState<Reviewer[]>([]),
    [exportPending, setExportPending] = useState<{
      format: string;
      mode: string;
    } | null>(null);
  // Document view: one QuestionDetail per question, fetched once and refreshed only when the
  // question's current answer changes. Filtering and sorting read from this cache.
  const detailCache = useRef(new Map<string, { answerId: string | null; detail: S['QuestionDetail'] }>());
  const [details, setDetails] = useState<Record<string, S['QuestionDetail']>>({}),
    [source, setSource] = useState<Source | null>(null);
  const { reload: reloadTender } = tender;
  const { reload: reloadQuestions } = questions;
  const refresh = useCallback(() => {
    void reloadTender();
    void reloadQuestions();
  }, [reloadTender, reloadQuestions]);
  useEffect(() => {
    let active = true;
    api<Reviewer[]>(`/api/w/${workspaceId}/tenders/${tenderId}/reviewers`, { method: 'GET' })
      .then((rows) => {
        if (active) setReviewers(rows);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [workspaceId, tenderId]);
  const rows = useMemo(
    () =>
      (questions.data ?? [])
        .filter(
          (q) =>
            (!status || (status === 'unapproved' ? q.status !== 'approved' : q.status === status)) &&
            (!coverage || q.coverage === coverage) &&
            (!owner || (owner === '-' ? !q.assignee : q.assignee === owner)) &&
            `${q.section} ${q.number} ${q.text} ${q.assignee ?? ''} ${q.compliance_class ?? ''}`
              .toLowerCase()
              .includes(search.toLowerCase()),
        )
        .sort((a, b) =>
          sort === 'weighting'
            ? (b.weighting ?? 0) - (a.weighting ?? 0)
            : sort === 'support'
              ? (a.current_answer?.support_summary.score ?? 0) - (b.current_answer?.support_summary.score ?? 0)
              : sort === 'assignee'
                ? (a.assignee ?? '').localeCompare(b.assignee ?? '')
                : a.order_index - b.order_index,
        ),
    [questions.data, status, coverage, search, sort, owner],
  );
  // The fetch is keyed on the visible ids and their current answer ids, so a filter keystroke
  // or a two-second job poll that changes neither refetches nothing and aborts nothing.
  const detailKey = view === 'document' ? rows.map((q) => `${q.id}:${q.current_answer?.id ?? ''}`).join(',') : '';
  useEffect(() => {
    if (!detailKey) return;
    const pending = detailKey
      .split(',')
      .map((entry) => {
        const [id, answerId] = entry.split(':');
        return { id, answerId: answerId || null };
      })
      .filter(({ id, answerId }) => {
        const cached = detailCache.current.get(id);
        return !cached || cached.answerId !== answerId;
      });
    if (!pending.length) return;
    const controller = new AbortController();
    let next = 0;
    const worker = async () => {
      while (next < pending.length && !controller.signal.aborted) {
        const { id, answerId } = pending[next++];
        const detail = await backendJson<S['QuestionDetail']>(workspaceId, `/questions/${id}`, 'GET', undefined, {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        detailCache.current.set(id, { answerId, detail });
        setDetails((current) => ({ ...current, [id]: detail }));
      }
    };
    // About six requests in flight at once, whatever the size of the tender.
    Promise.all(Array.from({ length: Math.min(6, pending.length) }, worker)).catch((e: unknown) => {
      if (!controller.signal.aborted) setError((e as Error).message);
    });
    return () => controller.abort();
  }, [detailKey, workspaceId]);
  async function act(action: string, body: unknown = {}) {
    setBusy(true);
    setError(null);
    try {
      const result = await backendJson<S['JobRecord']>(workspaceId, `/tenders/${tenderId}/${action}`, 'POST', body);
      if (result.kind) setJob(result.id);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function download(format: string, mode: string) {
    setExportPending(null);
    setBusy(true);
    setError(null);
    try {
      const res = await backendRequest(workspaceId, `/tenders/${tenderId}/export?format=${format}&mode=${mode}`);
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement('a');
      a.href = url;
      a.download = `${tender.data?.name ?? 'tender'}-${mode}.${format}`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const t = tender.data;
  const reviewersOf = (id: string) => reviewers.filter((r) => r.questionId === id);
  const owners = members.map((m) => m.displayName);
  const groups = groupQuestions(rows, groupBy, owners);
  async function patchTender(body: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      await backendJson(workspaceId, `/tenders/${tenderId}`, 'PATCH', body);
      refresh();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }
  async function move(questionId: string, group: string) {
    const q = rows.find((r) => r.id === questionId);
    if (!q) return;
    const body = groupBy === 'status' ? { status: group } : { assignee: group || null };
    if ((groupBy === 'status' && q.status === group) || (groupBy === 'owner' && (q.assignee ?? '') === group)) return;
    setNotice(null);
    try {
      await backendJson(workspaceId, `/questions/${questionId}`, 'PATCH', body);
      refresh();
    } catch (e) {
      const detail = e instanceof BackendRequestError ? (e.detail as { blockers?: unknown[]; detail?: { blockers?: unknown[] } }) : {};
      const blockers = detail.blockers ?? detail.detail?.blockers;
      setNotice(
        blockers?.length
          ? `Question ${q.number} can’t move to ${label(group)} yet: ${blockers.length} ${blockers.length === 1 ? 'thing needs' : 'things need'} resolving. Open the question to see what.`
          : (e as Error).message,
      );
    }
  }
  const hasPack = Boolean(t?.documents?.some((d) => d.tender_doc_kind === 'question_pack'));
  // Submission is offered once: an open tender that has never been submitted. The backend
  // refuses a repeat (409) and that message is shown if it arrives.
  const canSubmit = t?.status === 'open' && !t.submitted_at;
  const submitBlocker = !t
    ? null
    : t.submitted_at
      ? `Submitted ${formatDeadline(t.submitted_at)}. Approved answers are already in the library.`
      : t.status === 'archived'
        ? 'Archived tenders cannot be submitted. Restore it first.'
        : null;
  const noQuestions = questions.data !== null && (questions.data ?? []).length === 0;
  // The latest run first: triage follows extraction.
  const activeJob = job ?? t?.triage_job_id ?? t?.extract_job_id;
  return (
    <>
      <Link className="text-xs text-accent" href={`/w/${workspaceId}/tenders`}>
        ← All tenders
      </Link>
      <PageHeader
        title={t?.name ?? 'Tender'}
        subtitle={
          t ? `${t.buyer ?? 'Buyer not set'} · ${t.deadline ? `Deadline ${formatDeadline(t.deadline)}` : 'No deadline set'}` : 'Loading…'
        }
        actions={
          t ? (
            <div className="flex flex-wrap items-center gap-1.5">
              <DeadlineBadge days={t.days_remaining} submitted={t.status !== 'open'} />
              <Badge tone={t.status === 'open' ? 'blue' : 'neutral'}>{label(t.status)}</Badge>
              <Dropdown.Root>
                <Dropdown.Trigger className={buttonClasses('secondary', 'sm')} aria-label="Manage tender">
                  Manage <ChevronDown aria-hidden />
                </Dropdown.Trigger>
                <Dropdown.Portal>
                  <Dropdown.Content align="end" sideOffset={6} className="z-50 min-w-56 rounded-lg border border-line bg-bg p-1 text-ink shadow-lg">
                    <Dropdown.Item className={menuItem} onSelect={() => setEditing(true)}>
                      <Pencil aria-hidden /> Edit details
                    </Dropdown.Item>
                    {canSubmit ? (
                      <Dropdown.Item
                        className={menuItem}
                        onSelect={() => {
                          if (window.confirm('Mark this tender submitted and promote its approved answers into the library?')) void act('submit');
                        }}
                      >
                        <Send aria-hidden /> Mark as submitted
                      </Dropdown.Item>
                    ) : null}
                    {t.status === 'archived' ? (
                      <Dropdown.Item className={menuItem} onSelect={() => void patchTender({ status: 'open' })}>
                        <ArchiveRestore aria-hidden /> Restore from archive
                      </Dropdown.Item>
                    ) : (
                      <Dropdown.Item className={menuItem} onSelect={() => void patchTender({ status: 'archived' })}>
                        <Archive aria-hidden /> Archive
                      </Dropdown.Item>
                    )}
                    <Dropdown.Separator className="my-1 h-px bg-line" />
                    <Dropdown.Item
                      className={`${menuItem} text-red`}
                      disabled={!isAdmin || Boolean(t.submitted_at)}
                      onSelect={() => {
                        setConfirmName('');
                        setDeleting(true);
                      }}
                    >
                      <Trash2 aria-hidden /> Delete tender
                    </Dropdown.Item>
                    {!isAdmin || t.submitted_at ? (
                      <p className="px-2 pb-1 text-[11px] text-muted">
                        {t.submitted_at ? 'Submitted tenders are kept as a record; archive instead.' : 'Only admins can delete tenders.'}
                      </p>
                    ) : null}
                  </Dropdown.Content>
                </Dropdown.Portal>
              </Dropdown.Root>
            </div>
          ) : null
        }
      />
      <ErrorNote error={tender.error ?? questions.error ?? error} />
      {t?.status === 'archived' ? (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber/40 bg-amber-bg px-4 py-3 text-sm text-amber">
          <span>This tender is archived. It is hidden from your active tenders.</span>
          <Button size="sm" busy={busy} onClick={() => void patchTender({ status: 'open' })}>
            Restore
          </Button>
        </div>
      ) : null}
      {t && !hasPack ? (
        <div className={`${panel} max-w-2xl space-y-3`}>
          <h2 className="font-semibold">Upload the buyer’s question pack</h2>
          <p className="text-sm text-muted">
            Ten reads the pack, creates a card for every question and checks what your evidence library already covers. This usually takes
            about a minute.
          </p>
          <Upload workspaceId={workspaceId} tenderId={tenderId} onUploaded={refresh} kinds={['question_pack']} />
        </div>
      ) : null}
      {t && hasPack && noQuestions ? (
        <div className={`${panel} max-w-2xl space-y-2`}>
          <h2 className="font-semibold">Reading the question pack</h2>
          <p className="text-sm text-muted">Question cards appear here as they are found.</p>
          <JobProgress workspaceId={workspaceId} id={activeJob} onChange={refresh} />
        </div>
      ) : null}
      {t && hasPack && !noQuestions ? (
        <>
          <dl className="mb-4 grid grid-cols-2 gap-4 border-y border-line py-4 sm:grid-cols-4">
            {(
              [
                ['Approved', `${t.questions_approved} / ${t.questions_total}`, 'neutral'],
                ['Needs review', t.needs_review_count, t.needs_review_count ? 'red' : 'neutral'],
                ['Class C (cannot comply)', t.c_count, t.c_count ? 'red' : 'neutral'],
                ['Mandatory, not yet classified', t.unclassified_mandatory_count, t.unclassified_mandatory_count ? 'amber' : 'neutral'],
              ] as const
            ).map(([name, value, tone]) => (
              <div key={name}>
                <dt className="text-xs text-muted">{name}</dt>
                <dd className={`mt-1 text-2xl ${tone === 'red' ? 'text-red' : tone === 'amber' ? 'text-amber' : ''}`}>{value}</dd>
              </div>
            ))}
          </dl>
          <JobProgress workspaceId={workspaceId} id={activeJob} onChange={refresh} />
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div role="tablist" aria-label="Tender views" className="flex flex-wrap gap-1 rounded-lg bg-soft p-1">
              {VIEWS.map(([key, name]) => (
                <button
                  key={key}
                  role="tab"
                  aria-selected={view === key}
                  className={`rounded-md px-3 py-1.5 text-[13px] ${view === key ? 'bg-bg font-semibold shadow-sm' : 'text-muted hover:text-ink'}`}
                  onClick={() => setView(key)}
                >
                  {name}
                </button>
              ))}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" busy={busy} onClick={() => void act('retriage')} title="Re-check coverage for questions not yet started">
                Re-check coverage
              </Button>
              <Button size="sm" busy={busy} onClick={() => void act('draft-all', { include_new: false })}>
                Draft all covered questions
              </Button>
            </div>
          </div>
          {['board', 'list', 'document'].includes(view) ? (
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <input
                className={`${compact} w-56`}
                aria-label="Search questions"
                placeholder="Search questions or owners"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <select className={compact} aria-label="Status filter" value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="">Any status</option>
                <option value="unapproved">Not approved</option>
                {statuses.map((s) => (
                  <option key={s} value={s}>
                    {label(s)}
                  </option>
                ))}
              </select>
              <select className={compact} aria-label="Coverage filter" value={coverage} onChange={(e) => setCoverage(e.target.value)}>
                <option value="">Any coverage</option>
                {coverages.map((s) => (
                  <option key={s} value={s}>
                    {s === 'unknown' ? 'Pending' : label(s)}
                  </option>
                ))}
              </select>
              <select className={compact} aria-label="Owner filter" value={owner} onChange={(e) => setOwner(e.target.value)}>
                <option value="">Any owner</option>
                <option value="-">No owner</option>
                {owners.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>
              <span className="mx-1 h-5 w-px bg-line" aria-hidden />
              {view !== 'document' ? (
                <select className={compact} aria-label="Group by" value={groupBy} onChange={(e) => setGroupBy(e.target.value as GroupBy)}>
                  <option value="status">Group by status</option>
                  <option value="owner">Group by owner</option>
                  <option value="coverage">Group by coverage</option>
                  <option value="section">Group by section</option>
                </select>
              ) : null}
              <select className={compact} aria-label="Sort by" value={sort} onChange={(e) => setSort(e.target.value)}>
                <option value="order">Buyer’s order</option>
                <option value="weighting">Highest weighting</option>
                <option value="support">Lowest evidence coverage</option>
                <option value="assignee">Owner</option>
              </select>
              {search || status || coverage || owner ? (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setSearch('');
                    setStatus('');
                    setCoverage('');
                    setOwner('');
                  }}
                >
                  Clear filters
                </Button>
              ) : null}
              <span className="ml-auto text-xs text-muted">
                {rows.length} of {questions.data?.length ?? 0} questions
              </span>
            </div>
          ) : null}
          {notice ? (
            <div role="alert" className="mb-3 flex items-start justify-between gap-3 rounded-md border border-amber/40 bg-amber-bg px-3 py-2 text-xs text-amber">
              <span>{notice}</span>
              <button type="button" className="underline" onClick={() => setNotice(null)}>
                Dismiss
              </button>
            </div>
          ) : null}
        </>
      ) : null}
      {hasPack && !noQuestions && view === 'board' ? (
        <>
          {groupBy === 'status' || groupBy === 'owner' ? (
            <p className="mb-2 text-[11px] text-muted">
              Drag a card to another column to {groupBy === 'status' ? 'change its review status' : 'change its owner'}.
              {groupBy === 'status' ? ' Not started and AI draft are set by Ten.' : ''}
            </p>
          ) : null}
          <QuestionBoard
            workspaceId={workspaceId}
            groups={groups}
            reviewersOf={reviewersOf}
            canDrop={(g) => (groupBy === 'status' ? !['not_started', 'ai_draft'].includes(g) : groupBy === 'owner')}
            onMove={groupBy === 'status' || groupBy === 'owner' ? (id, g) => void move(id, g) : undefined}
          />
        </>
      ) : null}
      {hasPack && !noQuestions && view === 'list' ? (
        <QuestionList workspaceId={workspaceId} groups={groups} reviewersOf={reviewersOf} showStatus={groupBy !== 'status'} />
      ) : null}
      {hasPack && !noQuestions && view === 'document' ? (
        <div className={`grid gap-4 ${source ? 'xl:grid-cols-2' : ''}`}>
          <div className="space-y-5">
            {rows.map((q) => {
              const detail = details[q.id];
              return (
                <article className={panel} key={q.id}>
                  <Link className="text-sm font-semibold text-accent" href={questionLink(workspaceId, tenderId, q.id)}>
                    {q.section} · {q.number} · {q.text}
                  </Link>
                  <div className="my-2">
                    <StatusBadge status={q.status} />
                  </div>
                  {!detail ? (
                    <p className="text-sm text-muted" role="status">
                      Loading answer…
                    </p>
                  ) : detail.current_answer ? (
                    <Trace segments={detail.current_answer.segments} onSource={setSource} prefix={q.id} />
                  ) : (
                    <p className="text-sm text-muted">No answer yet.</p>
                  )}
                </article>
              );
            })}
          </div>
          {source ? (
            <SourcePane key={JSON.stringify(source.locator)} workspaceId={workspaceId} source={source} onClose={() => setSource(null)} />
          ) : null}
        </div>
      ) : null}
      {hasPack && !noQuestions && view === 'documents' ? (
        <div className="space-y-4">
          <div className="space-y-2">
            {t?.documents?.map((d) => (
              <Link
                className={`${panel} flex flex-wrap items-center justify-between gap-2 text-sm hover:bg-soft`}
                key={d.id}
                href={`/w/${workspaceId}/library/${d.id}`}
              >
                <span className="font-medium text-accent wrap-anywhere">{d.filename}</span>
                <span className="flex gap-1.5">
                  <Badge>{label(d.tender_doc_kind ?? 'document')}</Badge>
                  <Badge tone={d.ingest_status === 'ready' ? 'green' : d.ingest_status === 'failed' ? 'red' : 'blue'}>
                    {label(d.ingest_status)}
                  </Badge>
                </span>
              </Link>
            ))}
          </div>
          <div className={`${panel} space-y-2`}>
            <h2 className="text-sm font-semibold">Add a buyer document</h2>
            <Upload workspaceId={workspaceId} tenderId={tenderId} onUploaded={refresh} kinds={OTHER_KINDS} defaultKind="specification" />
          </div>
        </div>
      ) : null}
      {hasPack && !noQuestions && view === 'submission' ? (
        <section className={`${panel} space-y-5`}>
          <div>
            <h2 className="font-semibold">Export answers</h2>
            <p className="mt-1 text-sm text-muted">
              Submission exports contain approved answers in the buyer’s order, with a placeholder for anything not approved. Any question marked
              as needing review blocks the submission export. Review exports include every current answer.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {['docx', 'xlsx'].map((format) =>
              ['submission', 'review'].map((mode) => (
                <Button key={format + mode} busy={busy} onClick={() => setExportPending({ format, mode })}>
                  Export {mode} {format.toUpperCase()}
                </Button>
              )),
            )}
          </div>
          <Link className={buttonClasses('secondary', 'md', 'w-fit')} href={`/w/${workspaceId}/tenders/${tenderId}/submission`}>
            Fill the buyer’s original form
          </Link>
          <div className="border-t border-line pt-4">
            <h2 className="font-semibold">Submission and outcome</h2>
            <p className="mt-1 text-sm text-muted">Marking the tender submitted adds its approved answers to your evidence library.</p>
            <Button
              className="mt-3"
              busy={busy}
              disabled={!canSubmit}
              onClick={() => {
                if (window.confirm('Mark this tender submitted and promote its approved answers into the library?')) void act('submit');
              }}
            >
              Mark submitted
            </Button>
            {submitBlocker ? (
              <p className="mt-2 text-xs text-muted" role="status">
                {submitBlocker}
              </p>
            ) : null}
          </div>
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={async (e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              try {
                await backendJson(workspaceId, `/tenders/${tenderId}`, 'PATCH', {
                  outcome: f.get('outcome'),
                  outcome_notes: f.get('notes') || null,
                });
                refresh();
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          >
            <Field title="Outcome">
              <select className={field} name="outcome" defaultValue={t?.outcome}>
                {['pending', 'won', 'lost', 'unknown'].map((x) => (
                  <option key={x} value={x}>
                    {x === 'pending' ? 'Awaiting result' : x === 'unknown' ? 'Not known' : label(x)}
                  </option>
                ))}
              </select>
            </Field>
            <Field title="Outcome notes">
              <input className={field} name="notes" defaultValue={t?.outcome_notes ?? ''} />
            </Field>
            <Button type="submit">Save outcome</Button>
          </form>
        </section>
      ) : null}
      <Dialog open={editing} onOpenChange={setEditing} title="Edit tender details">
        {t ? (
          <form
            className="space-y-3"
            onSubmit={async (e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              const saved = await patchTender({
                name: String(f.get('name')),
                buyer: String(f.get('buyer') ?? '') || null,
                deadline: f.get('deadline') ? new Date(String(f.get('deadline'))).toISOString() : null,
                regime: f.get('regime') || null,
                is_framework: f.get('framework') === 'on',
              });
              if (saved) setEditing(false);
            }}
          >
            <Field title="Tender name">
              <input className={field} name="name" required maxLength={512} defaultValue={t.name} />
            </Field>
            <Field title="Buyer">
              <input className={field} name="buyer" maxLength={256} defaultValue={t.buyer ?? ''} />
            </Field>
            <Field title="Submission deadline (your local time)">
              <input className={field} name="deadline" type="datetime-local" defaultValue={toLocalInput(t.deadline)} />
            </Field>
            <Field title="Procurement regime">
              <select className={field} name="regime" defaultValue={t.regime ?? ''}>
                <option value="">Not set</option>
                {['procurement_act', 'psr', 'pcr_2015', 'other'].map((x) => (
                  <option key={x} value={x}>
                    {label(x)}
                  </option>
                ))}
              </select>
            </Field>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" name="framework" defaultChecked={Boolean(t.is_framework)} /> This is a framework agreement
            </label>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setEditing(false)}>
                Cancel
              </Button>
              <Button type="submit" variant="primary" busy={busy}>
                Save changes
              </Button>
            </div>
          </form>
        ) : null}
      </Dialog>
      <Dialog
        open={deleting}
        onOpenChange={setDeleting}
        title="Delete this tender?"
        description="This permanently removes the tender, its buyer documents, every question, answer, comment and reviewer. It cannot be undone. Archive it instead if you may need it later."
      >
        <form
          className="space-y-3"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError(null);
            try {
              await api(`/api/w/${workspaceId}/tenders/${tenderId}`, { method: 'DELETE' });
              router.push(`/w/${workspaceId}/tenders`);
            } catch (e) {
              setError((e as Error).message);
              setDeleting(false);
              setBusy(false);
            }
          }}
        >
          <Field title={`Type the tender name to confirm: ${t?.name ?? ''}`}>
            <input className={field} value={confirmName} onChange={(e) => setConfirmName(e.target.value)} />
          </Field>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setDeleting(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="danger" busy={busy} disabled={confirmName.trim() !== t?.name}>
              Delete tender
            </Button>
          </div>
        </form>
      </Dialog>
      <Dialog
        open={Boolean(exportPending)}
        onOpenChange={(open) => {
          if (!open) setExportPending(null);
        }}
        title="Export answers"
        description={`${t?.questions_approved ?? 0} of ${t?.questions_total ?? 0} questions are approved. Submission exports use placeholders for unapproved answers.`}
      >
        <Button
          variant="primary"
          onClick={() => {
            if (exportPending) void download(exportPending.format, exportPending.mode);
          }}
        >
          Download export
        </Button>
      </Dialog>
    </>
  );
}
