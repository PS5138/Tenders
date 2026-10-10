import { notFound, redirect } from 'next/navigation';
import { getSessionUser } from '@/server/auth';
import { backendIdentity } from '@/server/backend/context';
import { aiMode, getBackendHealth, getBusinessSynthetic } from '@/server/backend/health';
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
  const [health, synthetic] = await Promise.all([
    getBackendHealth().catch(() => null),
    backendIdentity(user.id, workspaceId)
      .then(getBusinessSynthetic)
      .catch(() => null),
  ]);
  return (
    <AppShell shell={shell} ai={aiMode(health, synthetic)}>
      {children}
    </AppShell>
  );
}
