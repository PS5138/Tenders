import { z } from 'zod';
import { mutation, parseJson, query, uuid } from '@/server/http';
import { listNotifications, markNotificationsRead } from '@/server/services/notifications';

export async function GET(req: Request, ctx: RouteContext<'/api/w/[workspaceId]/notifications'>) {
  const { workspaceId } = await ctx.params;
  const url = new URL(req.url);
  return query(({ user }) =>
    listNotifications(user.id, workspaceId, {
      limit: Number(url.searchParams.get('limit') ?? 30),
      unreadOnly: url.searchParams.get('unread') === '1',
    }),
  );
}

const body = z.object({ ids: z.union([z.literal('all'), z.array(uuid).max(500)]) });

export async function POST(req: Request, ctx: RouteContext<'/api/w/[workspaceId]/notifications'>) {
  const { workspaceId } = await ctx.params;
  return mutation(async ({ user }) => {
    const { ids } = await parseJson(req, body);
    return markNotificationsRead(user.id, workspaceId, ids);
  });
}
