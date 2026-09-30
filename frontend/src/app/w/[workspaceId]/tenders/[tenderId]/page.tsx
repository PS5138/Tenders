import { TenderWorkspace } from '@/features/backend/tenders';
import { requireUser } from '@/server/auth';
import { listActiveMembers } from '@/server/services/workspaces';
import { memberRole } from '@/server/services/tenders';

export default async function Page({ params }: PageProps<'/w/[workspaceId]/tenders/[tenderId]'>) {
  const { workspaceId, tenderId } = await params;
  const user = await requireUser();
  const [role, members] = await Promise.all([memberRole(user.id, workspaceId), listActiveMembers(user.id, workspaceId)]);
  return (
    <TenderWorkspace
      workspaceId={workspaceId}
      tenderId={tenderId}
      isAdmin={role === 'admin'}
      members={members.map((m) => ({ userId: m.userId, displayName: m.displayName }))}
    />
  );
}
