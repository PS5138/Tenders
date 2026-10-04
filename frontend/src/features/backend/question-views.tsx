'use client';
import { useState, type ReactNode } from 'react';
import Link from 'next/link';
import { ArrowDown, ArrowUp, ArrowUpDown, CircleCheck, CircleDashed, Flag, PenLine, ShieldCheck, Sparkles, UserRound, type LucideIcon } from 'lucide-react';
import { REVIEWER_ROLE_LABELS, type ReviewerRole } from '@/lib/review-roles';
import { formatEvidenceScore, supportedText } from '@/lib/format';
import { COVERAGE_ORDER, SORT_LABELS, STATUS_ORDER, wordUsage, type QuestionSort, type SortKey } from '@/lib/question-table';
import { Badge } from '@/components/ui/badge';
import { ClassBadge } from './badges';
import { label, type S } from './shared';

export type Question = S['QuestionListItem'];
export type Reviewer = { questionId: string; userId: string; displayName: string; role: ReviewerRole };
export type GroupBy = 'status' | 'owner' | 'coverage' | 'section';
export type Group = { key: string; label: string; icon?: ReactNode; questions: Question[] };

const STATUS_ICON: Record<string, { icon: LucideIcon; className: string }> = {
  not_started: { icon: CircleDashed, className: 'text-muted' },
  ai_draft: { icon: Sparkles, className: 'text-blue' },
  writer_edited: { icon: PenLine, className: 'text-violet' },
  sme_verified: { icon: ShieldCheck, className: 'text-amber' },
  approved: { icon: CircleCheck, className: 'text-accent' },
};
const COVERAGE_DOT: Record<string, string> = { covered: 'bg-accent', partial: 'bg-amber', new: 'bg-red', unknown: 'bg-line-strong' };
const coverageName = (c: string) => (c === 'unknown' ? 'Pending' : label(c));

export function StatusIcon({ status, className = '' }: { status: string; className?: string }) {
  const { icon: Icon, className: tone } = STATUS_ICON[status] ?? STATUS_ICON.not_started;
  return <Icon className={`size-4 shrink-0 ${tone} ${className}`} aria-label={label(status)} />;
}

function CoverageDot({ coverage }: { coverage: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-muted" title="Library coverage">
      <span className={`size-2 rounded-full ${COVERAGE_DOT[coverage] ?? 'bg-line-strong'}`} aria-hidden />
      {coverageName(coverage)}
    </span>
  );
}

export function Initials({ name, title }: { name: string; title?: string }) {
  return (
    <span
      title={title ?? name}
      className="inline-flex size-6 shrink-0 items-center justify-center rounded-full bg-tint text-[10px] font-semibold text-accent ring-2 ring-bg"
    >
      {name
        .split(/\s+/)
        .map((p) => p[0])
        .join('')
        .slice(0, 2)
        .toUpperCase()}
    </span>
  );
}

function People({ q, reviewers }: { q: Question; reviewers: Reviewer[] }) {
  return (
    <span className="flex shrink-0 -space-x-1.5">
      {q.assignee ? <Initials name={q.assignee} title={`Owner: ${q.assignee}`} /> : null}
      {reviewers.map((r) => (
        <Initials key={`${r.userId}-${r.role}`} name={r.displayName} title={`${REVIEWER_ROLE_LABELS[r.role]}: ${r.displayName}`} />
      ))}
    </span>
  );
}

export function groupQuestions(rows: Question[], by: GroupBy, owners: string[]): Group[] {
  if (by === 'status')
    return STATUS_ORDER.map((s) => ({ key: s, label: label(s), icon: <StatusIcon status={s} />, questions: rows.filter((q) => q.status === s) }));
  if (by === 'coverage')
    return COVERAGE_ORDER.map((c) => ({
      key: c,
      label: coverageName(c),
      icon: <span className={`size-2.5 rounded-full ${COVERAGE_DOT[c]}`} aria-hidden />,
      questions: rows.filter((q) => q.coverage === c),
    }));
  if (by === 'owner')
    return [
      ...owners.map((name) => ({ key: name, label: name, icon: <Initials name={name} />, questions: rows.filter((q) => q.assignee === name) })),
      {
        key: '',
        label: 'No owner',
        icon: <UserRound className="size-4 text-muted" aria-hidden />,
        questions: rows.filter((q) => !q.assignee || !owners.includes(q.assignee)),
      },
    ];
  const sections = [...new Set(rows.map((q) => q.section))];
  return sections.map((s) => ({ key: s, label: s, questions: rows.filter((q) => q.section === s) }));
}

const questionHref = (ws: string, q: Question) => `/w/${ws}/tenders/${q.tender_id}/responses/${q.id}`;

/** Evidence coverage of the current answer: "12 of 14 supported · 0.86". Nothing when there is no answer. */
function EvidenceCoverage({ q, className = '' }: { q: Question; className?: string }) {
  const summary = q.current_answer?.support_summary;
  if (!summary) return null;
  return (
    <span className={`tabular-nums ${className}`} title="Evidence coverage: the share of substantive sentences with a verified source">
      {supportedText(summary)} · {formatEvidenceScore(summary.score)}
    </span>
  );
}

/** "120 / 250 words", "No answer yet · 250 word limit" or "120 words". */
export function wordsText(q: Pick<Question, 'word_limit' | 'current_answer'>): string {
  const limit = q.word_limit;
  if (!q.current_answer) return limit ? `No answer yet · ${limit} word limit` : 'No answer yet';
  return `${q.current_answer.word_count}${limit ? ` / ${limit}` : ''} words`;
}

const COLUMNS: { key: SortKey; label: string; className?: string }[] = [
  { key: 'order', label: 'Question' },
  { key: 'status', label: 'Status' },
  { key: 'coverage', label: 'Coverage' },
  { key: 'compliance', label: 'Class' },
  { key: 'weighting', label: 'Weighting', className: 'text-right' },
  { key: 'assignee', label: 'Owner' },
  { key: 'words', label: 'Words / limit', className: 'text-right' },
  { key: 'support', label: 'Evidence coverage', className: 'text-right' },
];

/** The table view: one row per question, sortable by any column header. Rows arrive filtered and sorted. */
export function QuestionTable({
  workspaceId,
  rows,
  sort,
  onSort,
  reviewersOf,
}: {
  workspaceId: string;
  rows: Question[];
  sort: QuestionSort;
  onSort: (key: SortKey) => void;
  reviewersOf: (id: string) => Reviewer[];
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <table className="w-full min-w-[960px] border-collapse text-[13px]">
        <thead className="bg-soft text-left text-xs">
          <tr>
            {COLUMNS.map((c) => {
              const active = sort.key === c.key;
              const Arrow = sort.direction === 'asc' ? ArrowUp : ArrowDown;
              return (
                <th
                  key={c.key}
                  scope="col"
                  aria-sort={active ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}
                  className={`border-b border-line px-3 py-2 font-semibold ${c.className ?? ''}`}
                >
                  <button
                    type="button"
                    className={`inline-flex items-center gap-1 hover:text-ink ${active ? 'text-ink' : 'text-muted'}`}
                    onClick={() => onSort(c.key)}
                    title={`Sort by ${SORT_LABELS[c.key].toLowerCase()}`}
                  >
                    {c.label}
                    {active ? <Arrow className="size-3" aria-hidden /> : <ArrowUpDown className="size-3 opacity-40" aria-hidden />}
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((q) => {
            const usage = wordUsage(q);
            return (
              <tr key={q.id} className="border-b border-line last:border-0 hover:bg-soft">
                <td className="max-w-md px-3 py-2">
                  <Link href={questionHref(workspaceId, q)} className="block focus:outline-none focus:ring-2 focus:ring-accent">
                    <span className="block text-[11px] text-muted">
                      {q.section} · <span className="tabular-nums">{q.number}</span>
                    </span>
                    <span className="line-clamp-2 font-medium" title={q.text}>
                      {q.text}
                    </span>
                  </Link>
                  <span className="mt-1 flex flex-wrap gap-1.5">
                    {q.mandatory ? <Badge>Mandatory</Badge> : null}
                    {q.needs_review ? (
                      <Badge tone="red">
                        <Flag aria-hidden /> Needs review
                      </Badge>
                    ) : null}
                  </span>
                </td>
                <td className="px-3 py-2">
                  <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
                    <StatusIcon status={q.status} />
                    {label(q.status)}
                  </span>
                </td>
                <td className="px-3 py-2">
                  <CoverageDot coverage={q.coverage} />
                </td>
                <td className="px-3 py-2">{q.compliance_class ? <ClassBadge value={q.compliance_class} /> : <span className="text-muted">—</span>}</td>
                <td className="px-3 py-2 text-right tabular-nums">{q.weighting ?? <span className="text-muted">—</span>}</td>
                <td className="px-3 py-2">
                  <span className="flex items-center gap-2">
                    {q.assignee ? <span className="whitespace-nowrap">{q.assignee}</span> : <span className="text-muted">No owner</span>}
                    <People q={{ ...q, assignee: null }} reviewers={reviewersOf(q.id)} />
                  </span>
                </td>
                <td className={`whitespace-nowrap px-3 py-2 text-right tabular-nums ${usage != null && usage > 1 ? 'font-semibold text-red' : ''}`}>
                  {q.current_answer ? q.current_answer.word_count : <span className="text-muted">—</span>}
                  {q.word_limit ? <span className="text-muted"> / {q.word_limit}</span> : null}
                  {usage != null && usage > 1 ? <span className="sr-only"> (over the limit)</span> : null}
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-right text-xs">
                  {q.current_answer ? <EvidenceCoverage q={q} /> : <span className="text-muted">No answer yet</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Linear-style board. Cards can be dragged between columns when `onMove` accepts the group. */
export function QuestionBoard({
  workspaceId,
  groups,
  reviewersOf,
  canDrop,
  onMove,
}: {
  workspaceId: string;
  groups: Group[];
  reviewersOf: (id: string) => Reviewer[];
  canDrop: (group: string) => boolean;
  onMove?: (questionId: string, group: string) => void;
}) {
  const [dragging, setDragging] = useState<string | null>(null),
    [over, setOver] = useState<string | null>(null);
  return (
    <div className="-mx-1 flex gap-3 overflow-x-auto px-1 pb-3">
      {groups.map((g) => {
        const droppable = Boolean(onMove && dragging && canDrop(g.key));
        return (
          <section
            key={g.key}
            aria-label={g.label}
            className={`flex min-w-52 flex-1 basis-0 flex-col rounded-lg p-2 transition-colors ${over === g.key && droppable ? 'bg-tint ring-2 ring-accent' : 'bg-soft'} ${dragging && !droppable ? 'opacity-60' : ''}`}
            onDragOver={(e) => {
              if (!droppable) return;
              e.preventDefault();
              setOver(g.key);
            }}
            onDragLeave={() => setOver((o) => (o === g.key ? null : o))}
            onDrop={(e) => {
              e.preventDefault();
              const id = e.dataTransfer.getData('text/plain');
              setOver(null);
              setDragging(null);
              if (id && droppable) onMove?.(id, g.key);
            }}
          >
            <h2 className="flex items-center gap-2 px-1 pb-2 text-xs font-semibold">
              {g.icon}
              <span className="min-w-0 flex-1 truncate">{g.label}</span>
              <span className="font-normal text-muted">{g.questions.length}</span>
            </h2>
            <div className="max-h-[70vh] min-h-24 space-y-2 overflow-y-auto">
              {g.questions.map((q) => (
                <Link
                  key={q.id}
                  href={questionHref(workspaceId, q)}
                  draggable={Boolean(onMove)}
                  onDragStart={(e) => {
                    e.dataTransfer.setData('text/plain', q.id);
                    e.dataTransfer.effectAllowed = 'move';
                    setDragging(q.id);
                  }}
                  onDragEnd={() => {
                    setDragging(null);
                    setOver(null);
                  }}
                  className={`block rounded-lg border border-line bg-bg p-3 shadow-sm hover:border-line-strong ${dragging === q.id ? 'opacity-50' : ''}`}
                >
                  <div className="flex items-center gap-2 text-[11px] text-muted">
                    <StatusIcon status={q.status} className="size-3.5" />
                    <span className="tabular-nums">{q.number}</span>
                    <span className="min-w-0 flex-1 truncate">{q.section}</span>
                    {q.weighting ? <span>W{q.weighting}</span> : null}
                  </div>
                  <h3 className="my-1.5 line-clamp-3 text-[13px] font-medium leading-snug">{q.text}</h3>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <CoverageDot coverage={q.coverage} />
                    <ClassBadge value={q.compliance_class} />
                    {q.mandatory ? <Badge>Mandatory</Badge> : null}
                    {q.needs_review ? <Badge tone="red">Needs review</Badge> : null}
                  </div>
                  <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-muted">
                    <span className="min-w-0 space-y-0.5">
                      <span className="block tabular-nums">{wordsText(q)}</span>
                      <EvidenceCoverage q={q} className="block" />
                    </span>
                    <People q={q} reviewers={reviewersOf(q.id)} />
                  </div>
                </Link>
              ))}
              {!g.questions.length ? (
                <p className="px-1 py-6 text-center text-[11px] text-muted">{droppable ? 'Drop here' : 'None'}</p>
              ) : null}
            </div>
          </section>
        );
      })}
    </div>
  );
}
