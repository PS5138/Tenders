import type { Metadata } from 'next';
import { requireUser } from '@/server/auth';
import { listNotifications } from '@/server/services/notifications';
import { PageHeader } from '@/components/ui/page-header';
import { NotificationList } from '@/features/shell/notification-list';

export const metadata: Metadata = { title: 'Notifications' };

export default async function NotificationsPage({ params }: PageProps<'/w/[workspaceId]/notifications'>) {
  const { workspaceId } = await params;
  const user = await requireUser();
  const items = await listNotifications(user.id, workspaceId, { limit: 200 });
  return (
    <div className="max-w-3xl">
      <PageHeader
        title="Notifications"
        subtitle="Review requests, mentions, requested changes, completed reviews and finished drafts. Reading a notification does not resolve its task."
      />
      <NotificationList workspaceId={workspaceId} items={JSON.parse(JSON.stringify(items))} />
    </div>
  );
}
