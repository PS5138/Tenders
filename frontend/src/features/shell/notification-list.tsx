'use client';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/states';
import { api } from '@/lib/api-client';
import { formatDateTime } from '@/lib/format';
import { notificationHref } from '@/lib/links';

type Item = { id: string; summary: string; eventType: string; tenderId: string | null; responseId: string | null; readAt: string | null; createdAt: string; actorName: string | null };

const EVENT: Record<string, string> = {
  assigned: 'Question assigned',
  status_changed: 'Review status changed',
  comment_added: 'Comment',
  review_requested: 'Review request',
  mentioned: 'Mention',
  changes_requested: 'Changes requested',
  review_completed: 'Review completed',
  review_superseded: 'Review superseded',
  review_reassigned: 'Review reassigned',
  job_failed: 'Processing failed',
  draft_ready: 'Draft ready',
  recheck_required: 'Re-check needed',
};

export function NotificationList({ workspaceId, items }: { workspaceId: string; items: Item[] }) {
  const router = useRouter();
  if (!items.length) return <EmptyState title="No notifications">You will be notified when a question is assigned to you, its status changes, or a colleague comments for you.</EmptyState>;
  const unread = items.filter((i) => !i.readAt).length;
  return (
    <div>
      {unread ? (
        <Button
          className="mb-3"
          onClick={async () => {
            await api(`/api/w/${workspaceId}/notifications`, { body: { ids: 'all' } });
            router.refresh();
          }}
        >
          Mark all {unread} as read
        </Button>
      ) : null}
      <ul className="divide-y divide-line border-y border-line">
        {items.map((n) => (
          <li key={n.id} className="flex items-start gap-3 py-3 text-xs">
            <span className={`mt-1.5 size-2 shrink-0 rounded-full ${n.readAt ? 'bg-transparent' : 'bg-accent'}`} aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="text-muted">
                {EVENT[n.eventType] ?? n.eventType} · {formatDateTime(n.createdAt)}
                {!n.readAt ? <span className="sr-only"> (unread)</span> : null}
              </p>
              <Link
                href={notificationHref(workspaceId, n)}
                onClick={() => {
                  if (!n.readAt) void api(`/api/w/${workspaceId}/notifications`, { body: { ids: [n.id] } });
                }}
                className="mt-0.5 block text-[13px] text-ink hover:underline wrap-anywhere"
              >
                {n.summary}
              </Link>
            </div>
            {!n.readAt ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={async () => {
                  await api(`/api/w/${workspaceId}/notifications`, { body: { ids: [n.id] } });
                  router.refresh();
                }}
              >
                Mark read
              </Button>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
