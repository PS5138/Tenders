import type { Metadata } from 'next';
import { requireUser } from '@/server/auth';
import { getTeam } from '@/server/services/workspaces';
import { PageHeader } from '@/components/ui/page-header';
import { TeamView } from '@/features/team/team-view';

export const metadata: Metadata = { title: 'Team' };

export default async function TeamPage({ params }: PageProps<'/w/[workspaceId]/team'>) {
  const { workspaceId } = await params;
  const user = await requireUser();
  const { me, members, invitations } = await getTeam(user.id, workspaceId);
  return (
    <div className="max-w-4xl">
      <PageHeader
        eyebrow="Workspace"
        title="Your team"
        subtitle="Tender owners coordinate submissions. Reviewers approve individual responses. Only admins change membership and roles."
      />
      <TeamView
        workspaceId={workspaceId}
        me={{ userId: me.userId, role: me.role }}
        members={JSON.parse(JSON.stringify(members))}
        invitations={JSON.parse(JSON.stringify(invitations))}
      />
    </div>
  );
}
