import { proxyBackend } from '@/server/backend/proxy';

export const runtime = 'nodejs';

async function handle(request: Request, context: RouteContext<'/api/w/[workspaceId]/backend/[...path]'>) {
  const { workspaceId, path } = await context.params;
  return proxyBackend(request, workspaceId, path);
}

export { handle as GET, handle as POST, handle as PATCH, handle as DELETE };
