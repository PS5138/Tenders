'use client';
import { useState, type ReactNode } from 'react';
import Link from 'next/link';
import { ChevronDown, ChevronRight, CircleCheck, CircleDashed, Flag, PenLine, ShieldCheck, Sparkles, UserRound, type LucideIcon } from 'lucide-react';
import { REVIEWER_ROLE_LABELS, type ReviewerRole } from '@/lib/review-roles';
import { Badge } from '@/components/ui/badge';
import { ClassBadge } from './badges';
import { label, type S } from './shared';

export type Question = S['QuestionListItem'];
export type Reviewer = { questionId: string; userId: string; displayName: string; role: ReviewerRole };
export type GroupBy = 'status' | 'owner' | 'coverage' | 'section';
export type Group = { key: string; label: string; icon?: ReactNode; questions: Question[] };

export const STATUS_ORDER = ['not_started', 'ai_draft', 'writer_edited', 'sme_verified', 'approved'];
const STATUS_ICON: Record<string, { icon: LucideIcon; className: string }> = {
  not_started: { icon: CircleDashed, className: 'text-muted' },
  ai_draft: { icon: Sparkles, className: 'text-blue' },
  writer_edited: { icon: PenLine, className: 'text-violet' },
  sme_verified: { icon: ShieldCheck, className: 'text-amber' },
  approved: { icon: CircleCheck, className: 'text-accent' },
};
const COVERAGE_ORDER = ['covered', 'partial', 'new', 'unknown'];
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

function Evidence({ q }: { q: Question }) {
  const score = q.current_answer?.support_summary.score;
  return score == null ? null : (
    <span className="text-[11px] text-muted" title="Evidence coverage of the current answer">
      {Math.round(score * 100)}%
    </span>
  );
}

/** Linear-style list: groups of compact rows with properties on the right. */
export function QuestionList({
  workspaceId,
  groups,
  reviewersOf,
  showStatus,
}: {
  workspaceId: string;
  groups: Group[];
  reviewersOf: (id: string) => Reviewer[];
  showStatus: boolean;
}) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  return (
    <div className="overflow-hidden rounded-lg border border-line">
      {groups
        .filter((g) => g.questions.length)
        .map((g) => (
          <section key={g.key} aria-label={g.label}>
            <button
              type="button"
              className="flex w-full items-center gap-2 border-b border-line bg-soft px-3 py-2 text-left text-xs font-semibold"
              aria-expanded={!collapsed[g.key]}
              onClick={() => setCollapsed((c) => ({ ...c, [g.key]: !c[g.key] }))}
            >
              {collapsed[g.key] ? <ChevronRight className="size-3.5" aria-hidden /> : <ChevronDown className="size-3.5" aria-hidden />}
              {g.icon}
              {g.label}
              <span className="font-normal text-muted">{g.questions.length}</span>
            </button>
            {!collapsed[g.key] ? (
              <ul>
                {g.questions.map((q) => (
                  <li key={q.id} className="border-b border-line last:border-0">
                    <Link
                      href={questionHref(workspaceId, q)}
                      className="flex min-w-0 items-center gap-3 px-3 py-2 text-[13px] hover:bg-soft focus:bg-soft focus:outline-none"
                    >
                      {showStatus ? <StatusIcon status={q.status} /> : null}
                      <span className="w-10 shrink-0 text-[11px] tabular-nums text-muted">{q.number}</span>
                      <span className="min-w-0 flex-1 truncate" title={q.text}>
                        {q.text}
                      </span>
                      <span className="hidden shrink-0 items-center gap-2 md:flex">
                        {q.needs_review ? (
                          <Badge tone="red">
                            <Flag aria-hidden /> Needs review
                          </Badge>
                        ) : null}
                        {q.mandatory ? <Badge>Mandatory</Badge> : null}
                        <ClassBadge value={q.compliance_class} />
                        <CoverageDot coverage={q.coverage} />
                        {q.weighting ? <span className="w-10 text-right text-[11px] text-muted">W{q.weighting}</span> : null}
                        <span className="w-16 text-right text-[11px] tabular-nums text-muted" title="Words / limit">
                          {q.current_answer || q.word_limit ? `${q.current_answer?.word_count ?? 0}${q.word_limit ? `/${q.word_limit}` : ''}` : ''}
                        </span>
                        <span className="w-9 text-right">
                          <Evidence q={q} />
                        </span>
                      </span>
                      <People q={q} reviewers={reviewersOf(q.id)} />
                    </Link>
                  </li>
                ))}
              </ul>
            ) : null}
          </section>
        ))}
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
                    <span className="tabular-nums">
                      {q.current_answer ? `${q.current_answer.word_count}${q.word_limit ? ` / ${q.word_limit}` : ''} words` : 'No answer yet'}
                      {q.current_answer?.support_summary.score != null ? ` · ${Math.round(q.current_answer.support_summary.score * 100)}% evidence` : ''}
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
