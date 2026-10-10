'use client';
import { useCallback, useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, ArrowUpDown, Check, FileSearch, RefreshCw, Sparkles } from 'lucide-react';
import { backendJson } from '@/lib/backend-api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/states';
import {
  NO_REQUIREMENT_FILTERS,
  PRIORITY_LABELS,
  PRIORITY_ORDER,
  RATING_LABELS,
  RATING_MEANINGS,
  RATING_ORDER,
  REQUIREMENT_SORT_LABELS,
  classOf,
  filterRequirements,
  hasRequirementFilters,
  nextRequirementSort,
  ratingOf,
  ratingSummaryText,
  sortRequirements,
  type Rating,
  type RatingCounts,
  type RequirementFilters,
  type RequirementSort,
  type RequirementSortKey,
} from '@/lib/requirements';
import { ErrorNote, JobProgress, useResource, type S } from './shared';
import { SourcePane, type PaneSource, type Source } from './trace';

type Requirement = S['RequirementRecord'];
type Patch = { compliance_class?: 'A' | 'B' | 'C' | null; compliant_by?: string | null; comment?: string | null; owner?: string | null };

const compact = 'rounded-md border border-line bg-bg px-2 py-1.5 text-xs text-ink';
const without = (record: Record<string, string | boolean>, key: string) => Object.fromEntries(Object.entries(record).filter(([k]) => k !== key));
const RATING_DOT: Record<Rating, string> = { green: 'bg-accent', amber: 'bg-amber', red: 'bg-red', unrated: 'bg-line-strong' };
const RATING_TEXT: Record<Rating, string> = { green: 'text-accent', amber: 'text-amber', red: 'text-red', unrated: 'text-muted' };

/** A coloured dot that always sits beside its word: the rating is never shown by colour alone. */
export function RatingDot({ rating, className = '' }: { rating: Rating; className?: string }) {
  return <span className={`inline-block size-2.5 shrink-0 rounded-full ${RATING_DOT[rating]} ${className}`} aria-hidden />;
}

/** "41 Green · 6 Amber · 2 Red · 9 not rated" with a dot before each part. */
export function RatingSummary({ counts, className = '' }: { counts: RatingCounts; className?: string }) {
  if (!counts.total) return <span className={`text-muted ${className}`}>No requirements yet</span>;
  const parts = RATING_ORDER.filter((rating) => counts[rating] > 0);
  return (
    <span className={`inline-flex flex-wrap items-center gap-x-2.5 gap-y-1 ${className}`} aria-label={ratingSummaryText(counts)}>
      {parts.map((rating) => (
        <span key={rating} className="inline-flex items-center gap-1.5 whitespace-nowrap" aria-hidden>
          <RatingDot rating={rating} />
          <span className="tabular-nums">{counts[rating]}</span> {rating === 'unrated' ? 'not rated' : RATING_LABELS[rating]}
        </span>
      ))}
    </span>
  );
}

const paneSourceOf = (r: Requirement): PaneSource => ({
  locator: r.locator,
  quote: r.text,
  document_title: r.document_filename,
});

type Suggestion = { label_source?: string; rationale?: string; evidence?: Source[] };

function SuggestionPanel({
  requirement,
  busy,
  onAccept,
  onSource,
}: {
  requirement: Requirement;
  busy: boolean;
  onAccept: () => void;
  onSource: (source: PaneSource) => void;
}) {
  if (requirement.compliance_class) return null;
  const suggestion = (requirement.suggestion ?? {}) as Suggestion;
  const suggested = ratingOf(requirement.suggested_class);
  if (!suggestion.label_source) return null;
  if (suggested === 'unrated') {
    return (
      <p className="mt-1 text-[11px] text-muted">
        {suggestion.label_source === 'floor' ? 'No library evidence found.' : 'No suggestion from your library.'}
      </p>
    );
  }
  const evidence = suggestion.evidence ?? [];
  return (
    <div className="mt-1.5 space-y-1 text-[11px]">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="inline-flex items-center gap-1 text-muted">
          <Sparkles className="size-3" aria-hidden />
          Suggested:
          <RatingDot rating={suggested} className="size-2" />
          <span className={`font-medium ${RATING_TEXT[suggested]}`}>{RATING_LABELS[suggested]}</span>
        </span>
        <Button size="sm" className="px-1.5 py-0.5 text-[11px]" busy={busy} onClick={onAccept} aria-label={`Accept the suggested rating, ${RATING_LABELS[suggested]}`}>
          <Check aria-hidden /> Accept
        </Button>
      </div>
      <details className="group">
        <summary className="cursor-pointer text-accent">Why this suggestion?</summary>
        <div className="mt-1 space-y-1.5 rounded-md bg-soft p-2 text-ink">
          {suggestion.rationale ? <p>{suggestion.rationale}</p> : null}
          {evidence.map((source, i) => (
            <figure key={`${source.source_id}-${i}`} className="border-l-2 border-line-strong pl-2">
              <blockquote className="italic wrap-anywhere">“{source.quote}”</blockquote>
              <figcaption className="mt-0.5 flex flex-wrap items-center gap-1 text-muted">
                <span className="wrap-anywhere">{source.document_title}</span>
                <Button variant="link" size="sm" className="text-[11px]" onClick={() => onSource(source)}>
                  Open source
                </Button>
              </figcaption>
            </figure>
          ))}
          <p className="text-muted">A suggestion does not count until you accept it or choose a rating.</p>
        </div>
      </details>
    </div>
  );
}

/** The comment cell keeps its own draft and saves on blur; it is keyed by the saved value, so a save
 * elsewhere replaces it while a draft in progress is never overwritten by a poll. */
function CommentCell({ value, label: ariaLabel, onSave }: { value: string; label: string; onSave: (next: string | null) => Promise<boolean> }) {
  const [draft, setDraft] = useState(value);
  const commit = async () => {
    const next = draft.trim();
    if (next === value.trim()) return;
    const saved = await onSave(next || null);
    if (!saved) setDraft(value);
  };
  return (
    <textarea
      className={`${compact} min-h-9 w-full resize-y`}
      rows={1}
      aria-label={ariaLabel}
      placeholder="Add a note"
      maxLength={4000}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => void commit()}
      onKeyDown={(e) => {
        if (e.key === 'Escape') setDraft(value);
      }}
    />
  );
}

function SortHeader({
  name,
  sortKey,
  sort,
  onSort,
  className = '',
}: {
  name: string;
  sortKey?: RequirementSortKey;
  sort?: RequirementSort;
  onSort?: (key: RequirementSortKey) => void;
  className?: string;
}) {
  if (!sortKey || !sort || !onSort)
    return (
      <th scope="col" className={`border-b border-line px-3 py-2 font-semibold text-muted ${className}`}>
        {name}
      </th>
    );
  const active = sort.key === sortKey;
  const Arrow = sort.direction === 'asc' ? ArrowUp : ArrowDown;
  return (
    <th
      scope="col"
      aria-sort={active ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}
      className={`border-b border-line px-3 py-2 font-semibold ${className}`}
    >
      <button
        type="button"
        className={`inline-flex items-center gap-1 hover:text-ink ${active ? 'text-ink' : 'text-muted'}`}
        onClick={() => onSort(sortKey)}
        title={`Sort by ${REQUIREMENT_SORT_LABELS[sortKey].toLowerCase()}`}
      >
        {name}
        {active ? <Arrow className="size-3" aria-hidden /> : <ArrowUpDown className="size-3 opacity-40" aria-hidden />}
      </button>
    </th>
  );
}

/**
 * The Specification tab: every requirement the buyer's documents state, one row each, rated Green,
 * Amber or Red by a person. AI suggestions sit beside the rating and count only once accepted.
 */
export function SpecificationView({
  workspaceId,
  tenderId,
  owners,
  onChanged,
  onOpenDocuments,
}: {
  workspaceId: string;
  tenderId: string;
  owners: string[];
  onChanged?: () => void;
  onOpenDocuments?: () => void;
}) {
  const list = useResource<S['RequirementList']>(workspaceId, `/tenders/${tenderId}/requirements`);
  const [filters, setFilters] = useState<RequirementFilters>(NO_REQUIREMENT_FILTERS),
    [sort, setSort] = useState<RequirementSort>({ key: 'order', direction: 'asc' }),
    [saving, setSaving] = useState<Record<string, boolean>>({}),
    [rowErrors, setRowErrors] = useState<Record<string, string>>({}),
    [patched, setPatched] = useState<Record<string, Requirement>>({}),
    [rescanJob, setRescanJob] = useState<string | null>(null),
    [rescanning, setRescanning] = useState(false),
    [error, setError] = useState<string | null>(null),
    [source, setSource] = useState<PaneSource | null>(null);
  const { reload } = list;
  const refresh = useCallback(() => {
    void reload();
    onChanged?.();
  }, [reload, onChanged]);
  // A saved row is shown as the server returned it until the next load catches up.
  const all = useMemo(
    () =>
      (list.data?.requirements ?? []).map((r) => {
        const local = patched[r.id];
        return local && local.updated_at > r.updated_at ? local : r;
      }),
    [list.data, patched],
  );
  const rows = useMemo(() => sortRequirements(filterRequirements(all, filters), sort), [all, filters, sort]);
  const summary = list.data?.summary;
  const jobId = rescanJob ?? list.data?.job ?? null;

  async function save(requirement: Requirement, body: Patch): Promise<boolean> {
    setSaving((s) => ({ ...s, [requirement.id]: true }));
    setRowErrors((errs) => without(errs, requirement.id) as Record<string, string>);
    try {
      const saved = await backendJson<Requirement>(workspaceId, `/requirements/${requirement.id}`, 'PATCH', body);
      setPatched((p) => ({ ...p, [saved.id]: saved }));
      refresh();
      return true;
    } catch (e) {
      setRowErrors((errs) => ({ ...errs, [requirement.id]: (e as Error).message }));
      return false;
    } finally {
      setSaving((s) => without(s, requirement.id) as Record<string, boolean>);
    }
  }

  async function rescan() {
    setRescanning(true);
    setError(null);
    try {
      const result = await backendJson<S['RescanResponse']>(workspaceId, `/tenders/${tenderId}/requirements/rescan`, 'POST', {});
      setRescanJob(result.job_id);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRescanning(false);
    }
  }

  const setFilter = (change: Partial<RequirementFilters>) => setFilters((f) => ({ ...f, ...change }));
  const ownerOptions = (current: string | null | undefined) => (current && !owners.includes(current) ? [current, ...owners] : owners);
  const rescanButton = (
    <Button size="sm" busy={rescanning} onClick={() => void rescan()} title="Read every buyer document again for requirements">
      <RefreshCw aria-hidden /> Re-scan documents
    </Button>
  );

  return (
    <section aria-label="Specification requirements" className="space-y-3">
      <ErrorNote error={list.error ?? error} />
      <JobProgress workspaceId={workspaceId} id={jobId} onChange={refresh} />
      {summary && summary.total ? (
        <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
          <RatingSummary counts={summary} />
          {summary.suggested_unrated ? (
            <span className="text-xs text-muted">
              {summary.suggested_unrated} unrated {summary.suggested_unrated === 1 ? 'requirement has' : 'requirements have'} an AI suggestion
              to review.
            </span>
          ) : null}
        </div>
      ) : null}
      {list.data && !all.length ? (
        <EmptyState
          title="No specification requirements yet"
          action={
            <>
              {onOpenDocuments ? (
                <Button size="sm" variant="primary" onClick={onOpenDocuments}>
                  Upload the specification
                </Button>
              ) : null}
              {rescanButton}
            </>
          }
        >
          Upload the buyer’s specification, contract terms or clarification log under Buyer documents. Ten reads every buyer document and
          lists the requirements they state, so you can rate each one Green, Amber or Red.
        </EmptyState>
      ) : null}
      {!list.data && !list.error ? (
        <p className="text-sm text-muted" role="status">
          Loading requirements…
        </p>
      ) : null}
      {all.length ? (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <input
              className={`${compact} w-full sm:w-56`}
              aria-label="Search requirements"
              placeholder="Search requirements, notes or owners"
              value={filters.search}
              onChange={(e) => setFilter({ search: e.target.value })}
            />
            <Button
              size="sm"
              variant={filters.attention ? 'primary' : 'secondary'}
              aria-pressed={filters.attention}
              onClick={() => setFilter({ attention: !filters.attention })}
              title="Every Red, and every Must nobody has rated yet"
            >
              Reds and unrated Musts
            </Button>
            <select className={compact} aria-label="RAG filter" value={filters.rating} onChange={(e) => setFilter({ rating: e.target.value })}>
              <option value="">Any RAG rating</option>
              {RATING_ORDER.map((rating) => (
                <option key={rating} value={rating}>
                  {RATING_LABELS[rating]}
                </option>
              ))}
            </select>
            <select className={compact} aria-label="Priority filter" value={filters.priority} onChange={(e) => setFilter({ priority: e.target.value })}>
              <option value="">Any priority</option>
              {PRIORITY_ORDER.map((p) => (
                <option key={p} value={p}>
                  {PRIORITY_LABELS[p]}
                </option>
              ))}
              <option value="-">Not stated</option>
            </select>
            {hasRequirementFilters(filters) ? (
              <Button size="sm" variant="ghost" onClick={() => setFilters(NO_REQUIREMENT_FILTERS)}>
                Clear filters
              </Button>
            ) : null}
            <span className="ml-auto flex flex-wrap items-center gap-3">
              <span className="text-xs text-muted">
                {rows.length} of {all.length} requirements
              </span>
              {rescanButton}
            </span>
          </div>
          <div className={source ? 'grid gap-4 xl:grid-cols-[minmax(0,1fr)_26rem]' : ''}>
            <div className="min-w-0 overflow-x-auto rounded-lg border border-line">
              <table className="w-full min-w-[1080px] border-collapse text-[13px]">
                <thead className="bg-soft text-left text-xs">
                  <tr>
                    <SortHeader name="Ref" sortKey="order" sort={sort} onSort={(k) => setSort(nextRequirementSort(sort, k))} className="w-20" />
                    <SortHeader name="Requirement" />
                    <SortHeader name="Priority" sortKey="priority" sort={sort} onSort={(k) => setSort(nextRequirementSort(sort, k))} className="w-24" />
                    <SortHeader name="RAG" sortKey="rating" sort={sort} onSort={(k) => setSort(nextRequirementSort(sort, k))} className="w-60" />
                    <SortHeader name="Compliant by" className="w-40" />
                    <SortHeader name="Comment" className="w-60" />
                    <SortHeader name="Owner" sortKey="owner" sort={sort} onSort={(k) => setSort(nextRequirementSort(sort, k))} className="w-44" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const rating = ratingOf(r.compliance_class);
                    const busy = Boolean(saving[r.id]);
                    const name = r.ref ? `requirement ${r.ref}` : 'this requirement';
                    return (
                      <tr key={r.id} className="border-b border-line align-top last:border-0">
                        <td className="whitespace-nowrap px-3 py-2 tabular-nums">{r.ref ?? <span className="text-muted">—</span>}</td>
                        <td className="max-w-xl px-3 py-2">
                          <p className="whitespace-pre-wrap wrap-anywhere">{r.text}</p>
                          <div className="mt-1 flex flex-wrap items-center gap-x-2 text-[11px] text-muted">
                            <button
                              type="button"
                              className="inline-flex min-w-0 max-w-full items-center gap-1 text-left text-accent hover:underline"
                              onClick={() => setSource(paneSourceOf(r))}
                              aria-label={`Open ${name} in ${r.document_filename}`}
                              title={r.document_filename}
                            >
                              <FileSearch className="size-3 shrink-0" aria-hidden />
                              <span className="truncate">View in {r.document_filename}</span>
                            </button>
                            {r.locator.cell_ref ? <span>Row {r.locator.cell_ref}</span> : null}
                            {r.locator.heading_path?.length ? <span className="wrap-anywhere">{r.locator.heading_path.join(' / ')}</span> : null}
                          </div>
                          <div aria-live="polite" className="text-[11px]">
                            {busy ? <span className="text-muted">Saving…</span> : null}
                            {rowErrors[r.id] ? (
                              <span role="alert" className="text-red">
                                {rowErrors[r.id]}
                              </span>
                            ) : null}
                          </div>
                        </td>
                        <td className="px-3 py-2">
                          {r.priority ? <Badge tone={r.priority === 'must' ? 'blue' : 'neutral'}>{PRIORITY_LABELS[r.priority]}</Badge> : <span className="text-muted">—</span>}
                        </td>
                        <td className="px-3 py-2">
                          <label className="flex items-center gap-1.5">
                            <RatingDot rating={rating} />
                            <span className="sr-only">RAG rating for {name}</span>
                            <select
                              className={`${compact} w-full`}
                              value={rating}
                              disabled={busy}
                              title={RATING_MEANINGS[rating]}
                              onChange={(e) => void save(r, { compliance_class: classOf(e.target.value as Rating) })}
                            >
                              {RATING_ORDER.map((option) => (
                                <option key={option} value={option}>
                                  {RATING_LABELS[option]}
                                  {option === 'unrated' ? '' : ` · ${RATING_MEANINGS[option].toLowerCase()}`}
                                </option>
                              ))}
                            </select>
                          </label>
                          {r.rated_by ? (
                            <p className="mt-1 text-[11px] text-muted">
                              {RATING_LABELS[rating]} set by {r.rated_by}
                            </p>
                          ) : null}
                          <SuggestionPanel
                            requirement={r}
                            busy={busy}
                            onAccept={() => void save(r, { compliance_class: r.suggested_class ?? null })}
                            onSource={setSource}
                          />
                        </td>
                        <td className="px-3 py-2">
                          {rating === 'amber' ? (
                            <input
                              key={r.compliant_by ?? ''}
                              className={`${compact} w-full`}
                              type="date"
                              aria-label={`Compliant by date for ${name}`}
                              defaultValue={r.compliant_by ?? ''}
                              disabled={busy}
                              onBlur={(e) => {
                                const next = e.target.value || null;
                                if (next !== (r.compliant_by ?? null)) void save(r, { compliant_by: next });
                              }}
                            />
                          ) : (
                            <span className="text-muted">—</span>
                          )}
                        </td>
                        <td className="px-3 py-2">
                          <CommentCell
                            key={r.comment ?? ''}
                            value={r.comment ?? ''}
                            label={`Comment on ${name}`}
                            onSave={(comment) => save(r, { comment })}
                          />
                        </td>
                        <td className="px-3 py-2">
                          <select
                            className={`${compact} w-full`}
                            aria-label={`Owner of ${name}`}
                            value={r.owner ?? ''}
                            disabled={busy}
                            onChange={(e) => void save(r, { owner: e.target.value || null })}
                          >
                            <option value="">No owner</option>
                            {ownerOptions(r.owner).map((o) => (
                              <option key={o} value={o}>
                                {o}
                              </option>
                            ))}
                          </select>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {!rows.length ? <p className="p-4 text-sm text-muted">No requirements match these filters.</p> : null}
            </div>
            {source ? (
              <SourcePane key={JSON.stringify(source.locator)} workspaceId={workspaceId} source={source} onClose={() => setSource(null)} />
            ) : null}
          </div>
          <p className="text-[11px] text-muted">
            Green: compliant now. Amber: compliant by a date, in part or with a workaround. Red: cannot comply. A Red on a Must can fail the
            bid.
          </p>
        </>
      ) : null}
    </section>
  );
}
