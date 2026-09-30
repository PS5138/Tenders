import { mutation } from '@/server/http';
import { deleteTender } from '@/server/services/tenders';

export async function DELETE(_request: Request, ctx: RouteContext<'/api/w/[workspaceId]/tenders/[tenderId]'>) {
  const { workspaceId, tenderId } = await ctx.params;
  return mutation(({ user }) => deleteTender(user.id, workspaceId, tenderId));
}
