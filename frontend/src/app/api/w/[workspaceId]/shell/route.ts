import { query } from '@/server/http';
import { getShell } from '@/server/services/workspaces';

export async function GET(_req: Request, ctx: RouteContext<'/api/w/[workspaceId]/shell'>) {
  const { workspaceId } = await ctx.params;
  return query(async ({ user }) => {
    const shell = await getShell(user.id, workspaceId);
    return { openReviewCount: shell.openReviewCount, unreadNotificationCount: shell.unreadNotificationCount };
  });
}
