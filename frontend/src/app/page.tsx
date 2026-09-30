import { redirect } from 'next/navigation';
import { getSessionUser } from '@/server/auth';
import { listMyWorkspaces } from '@/server/services/workspaces';
import { NoWorkspace } from './no-workspace';

export default async function Home() {
  const user = await getSessionUser();
  if (!user) redirect('/login');
  const workspaces = await listMyWorkspaces(user.id);
  if (workspaces.length > 0) redirect(`/w/${workspaces[0].id}/tenders`);
  return <NoWorkspace email={user.email} />;
}
