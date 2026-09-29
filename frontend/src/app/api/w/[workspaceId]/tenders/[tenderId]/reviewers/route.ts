import { query } from '@/server/http';
import { listTenderReviewers } from '@/server/services/collaboration';

export async function GET(_request: Request, ctx: RouteContext<'/api/w/[workspaceId]/tenders/[tenderId]/reviewers'>) {
  const { workspaceId, tenderId } = await ctx.params;
  return query(({ user }) => listTenderReviewers(user.id, workspaceId, tenderId));
}
