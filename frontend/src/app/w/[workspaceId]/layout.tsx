import { notFound, redirect } from 'next/navigation';
import { getSessionUser } from '@/server/auth';
import { getBackendHealth } from '@/server/backend/health';
import { AppError } from '@/server/errors';
import { getShell } from '@/server/services/workspaces';
import { AppShell } from '@/features/shell/app-shell';

export default async function WorkspaceLayout({ children, params }: LayoutProps<'/w/[workspaceId]'>) {
  const { workspaceId } = await params;
  const user = await getSessionUser();
  if (!user) redirect('/login');
  let shell;
  try {
    shell = await getShell(user.id, workspaceId);
  } catch (err) {
    if (err instanceof AppError && err.code === 'NOT_FOUND') notFound();
    throw err;
  }
  return (
    <AppShell shell={shell} ai={await getBackendHealth().catch(() => ({ status: 'unavailable' }))}>
      {children}
    </AppShell>
  );
}
