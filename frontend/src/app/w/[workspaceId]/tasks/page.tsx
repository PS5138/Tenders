import type { ReactNode } from 'react';
import Link from 'next/link';
import { AtSign, CircleCheck, Clock, MessageSquare } from 'lucide-react';
import { requireUser } from '@/server/auth';
import { myTasks, type TaskItem } from '@/server/services/tasks';
import { REVIEWER_ROLE_LABELS } from '@/lib/review-roles';
import { PageHeader } from '@/components/ui/page-header';
import { Badge } from '@/components/ui/badge';
import { StatusBadge } from '@/features/backend/badges';

const questionHref = (ws: string, tenderId: string, questionId: string, thread?: string) =>
  `/w/${ws}/tenders/${tenderId}/responses/${questionId}${thread ? `?thread=${thread}` : ''}`;

function Due({ days }: { days: number | null }) {
  if (days == null) return null;
  if (days < 0) return <Badge tone="red">Overdue</Badge>;
  if (days === 0) return <Badge tone="red">Due today</Badge>;
  return <Badge tone={days <= 7 ? 'amber' : 'neutral'}>{`Due in ${days} ${days === 1 ? 'day' : 'days'}`}</Badge>;
}

function Section({ title, icon, count, empty, children }: { title: string; icon: ReactNode; count: number; empty: string; children: ReactNode }) {
  return (
    <section className="space-y-2" aria-label={title}>
      <h2 className="flex items-center gap-2 text-sm font-semibold">
        {icon}
        {title}
        <span className="font-normal text-muted">{count}</span>
      </h2>
      {count ? (
        <ul className="divide-y divide-line overflow-hidden rounded-lg border border-line">{children}</ul>
      ) : (
        <p className="rounded-lg border border-dashed border-line px-4 py-5 text-center text-xs text-muted">{empty}</p>
      )}
    </section>
  );
}

function TaskRow({ workspaceId, item, highlight }: { workspaceId: string; item: TaskItem; highlight: boolean }) {
  const q = item.question;
  const shown = highlight ? item.turns.filter((t) => t.turn) : item.turns;
  return (
    <li>
      <Link href={questionHref(workspaceId, q.tender_id, q.id)} className="block px-4 py-3 hover:bg-soft focus:bg-soft focus:outline-none">
        <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted">
          <span className="font-medium text-ink">{q.tenderName}</span>· {q.section} · {q.number}
          <span className="ml-auto flex gap-1.5">
            <Due days={q.tenderDaysRemaining} />
            <StatusBadge status={q.status} />
          </span>
        </p>
        <p className="my-1 line-clamp-2 text-sm font-medium">{q.text}</p>
        <ul className="space-y-0.5">
          {shown.map((t) => (
            <li key={t.role} className="flex flex-wrap items-center gap-2 text-xs">
              <Badge tone={t.role === 'owner' ? 'blue' : 'violet'}>{t.role === 'owner' ? 'Owner' : REVIEWER_ROLE_LABELS[t.role]}</Badge>
              <span className={highlight ? 'text-ink' : 'text-muted'}>{t.reason}</span>
            </li>
          ))}
        </ul>
      </Link>
    </li>
  );
}

export default async function Page({ params }: PageProps<'/w/[workspaceId]/tasks'>) {
  const { workspaceId } = await params;
  const user = await requireUser();
  const { yourTurn, replies, comingUp } = await myTasks(user.id, workspaceId);
  return (
    <>
      <PageHeader
        title="My tasks"
        subtitle="What is waiting on you across open tenders. Items leave this list when the work is done, not when you read a notification."
      />
      <div className="max-w-4xl space-y-8">
        <Section
          title="Your turn"
          icon={<CircleCheck className="size-4 text-accent" aria-hidden />}
          count={yourTurn.length}
          empty="Nothing is waiting on you right now."
        >
          {yourTurn.map((item) => (
            <TaskRow key={item.question.id} workspaceId={workspaceId} item={item} highlight />
          ))}
        </Section>
        <Section
          title="Replies waiting for you"
          icon={<MessageSquare className="size-4 text-blue" aria-hidden />}
          count={replies.length}
          empty="No comment threads are waiting for your reply."
        >
          {replies.map((r) => (
            <li key={r.threadId}>
              <Link
                href={questionHref(workspaceId, r.question.tender_id, r.question.id, r.threadId)}
                className="block px-4 py-3 hover:bg-soft focus:bg-soft focus:outline-none"
              >
                <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted">
                  <span className="font-medium text-ink">{r.question.tenderName}</span>· {r.question.number} ·{' '}
                  {r.question.text.length > 80 ? `${r.question.text.slice(0, 80)}…` : r.question.text}
                  {r.mentioned ? (
                    <Badge tone="blue" className="ml-auto">
                      <AtSign aria-hidden /> Mentioned you
                    </Badge>
                  ) : null}
                </p>
                <p className="mt-1 line-clamp-2 text-sm">
                  <span className="font-semibold">{r.lastAuthor}:</span> {r.lastBody}
                </p>
                <p className="mt-0.5 text-[11px] text-muted">
                  {r.messageCount} {r.messageCount === 1 ? 'message' : 'messages'} · last{' '}
                  {r.lastAt.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}
                </p>
              </Link>
            </li>
          ))}
        </Section>
        <Section title="Coming up" icon={<Clock className="size-4 text-muted" aria-hidden />} count={comingUp.length} empty="Nothing else assigned to you.">
          {comingUp.map((item) => (
            <TaskRow key={item.question.id} workspaceId={workspaceId} item={item} highlight={false} />
          ))}
        </Section>
      </div>
    </>
  );
}
