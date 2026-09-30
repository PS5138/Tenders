import { z } from 'zod';
import { mutation, parseJson, query, uuid } from '@/server/http';
import { addReviewer, listQuestionReviewers, removeReviewer, REVIEWER_ROLES } from '@/server/services/collaboration';

const body = z.object({ action: z.enum(['add', 'remove']), userId: uuid, role: z.enum(REVIEWER_ROLES) });

export async function GET(_request: Request, ctx: RouteContext<'/api/w/[workspaceId]/questions/[questionId]/reviewers'>) {
  const { workspaceId, questionId } = await ctx.params;
  return query(({ user }) => listQuestionReviewers(user.id, workspaceId, questionId));
}

export async function POST(request: Request, ctx: RouteContext<'/api/w/[workspaceId]/questions/[questionId]/reviewers'>) {
  const { workspaceId, questionId } = await ctx.params;
  return mutation(async ({ user }) => {
    const { action, ...input } = await parseJson(request, body);
    return action === 'add'
      ? addReviewer(user.id, workspaceId, questionId, input)
      : removeReviewer(user.id, workspaceId, questionId, input);
  });
}
