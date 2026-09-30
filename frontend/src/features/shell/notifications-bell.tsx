'use client';
import { useCallback, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import * as Popover from '@radix-ui/react-popover';
import { Bell } from 'lucide-react';
import { api, errorMessage } from '@/lib/api-client';
import { relativeTime } from '@/lib/format';
import { links, notificationHref } from '@/lib/links';
import { Button } from '@/components/ui/button';
import { ErrorState, Spinner } from '@/components/ui/states';
import { useShellCounts } from './shell-counts';

type Item = {
  id: string;
  summary: string;
  tenderId: string | null;
  responseId: string | null;
  readAt: string | null;
  createdAt: string;
};

export function NotificationsBell({ workspaceId }: { workspaceId: string }) {
  const router = useRouter();
  const { counts, refresh } = useShellCounts();
  const [items, setItems] = useState<Item[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setItems(await api<Item[]>(`/api/w/${workspaceId}/notifications?limit=15`, { method: 'GET' }));
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [workspaceId]);

  async function open(item: Item) {
    if (!item.readAt) {
      await api(`/api/w/${workspaceId}/notifications`, { body: { ids: [item.id] } }).catch(() => {});
    }
    router.push(notificationHref(workspaceId, item));
  }

  const unread = counts.unreadNotificationCount;
  return (
    <Popover.Root onOpenChange={(o) => (o ? void load() : undefined)}>
      <Popover.Trigger
        className="relative rounded-md p-2 text-ink hover:bg-soft"
        aria-label={unread ? `Notifications, ${unread} unread` : 'Notifications'}
      >
        <Bell className="size-4" aria-hidden />
        {unread ? (
          <span className="absolute -right-0.5 -top-0.5 grid min-w-4 place-items-center rounded-full bg-accent px-1 text-[10px] font-semibold text-on-accent">
            {unread > 99 ? '99+' : unread}
          </span>
        ) : null}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="end" sideOffset={6} className="z-50 w-[min(380px,calc(100vw-24px))] rounded-lg border border-line bg-bg p-2 text-ink shadow-lg">
          <div className="flex items-center justify-between px-2 py-1.5">
            <h2 className="text-sm font-semibold">Notifications</h2>
            {unread ? (
              <Button
                variant="link"
                size="sm"
                onClick={async () => {
                  await api(`/api/w/${workspaceId}/notifications`, { body: { ids: 'all' } });
                  await load();
                  refresh();
                }}
              >
                Mark all read
              </Button>
            ) : null}
          </div>
          <div className="max-h-[60vh] overflow-y-auto">
            {error ? <ErrorState message={error} onRetry={load} /> : null}
            {!items && !error ? <Spinner className="px-2 py-4" /> : null}
            {items && items.length === 0 ? <p className="px-2 py-6 text-center text-xs text-muted">No notifications yet.</p> : null}
            {items?.map((n) => (
              <button
                key={n.id}
                type="button"
                onClick={() => open(n)}
                className="flex w-full items-start gap-2 rounded-md px-2 py-2 text-left text-xs hover:bg-soft"
              >
                <span className={`mt-1 size-2 shrink-0 rounded-full ${n.readAt ? 'bg-transparent' : 'bg-accent'}`} aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className={`block wrap-anywhere ${n.readAt ? 'text-muted' : 'text-ink'}`}>
                    {!n.readAt ? <span className="sr-only">Unread: </span> : null}
                    {n.summary}
                  </span>
                  <span className="text-[11px] text-muted">{relativeTime(n.createdAt)}</span>
                </span>
              </button>
            ))}
          </div>
          <div className="border-t border-line px-2 pt-2">
            <Link href={links.notifications(workspaceId)} className="text-xs font-medium text-accent hover:underline">
              View all notifications
            </Link>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
