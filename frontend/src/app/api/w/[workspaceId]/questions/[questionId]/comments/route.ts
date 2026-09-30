import { z } from 'zod';
import { mutation, parseJson, query, uuid } from '@/server/http';
import { createCommentThread, listCommentThreads } from '@/server/services/collaboration';

const point = z.object({ segment: z.number().int().min(0), offset: z.number().int().min(0) });
const body = z.object({
  answerId: uuid,
  anchor: z.object({ start: point, end: point }),
  body: z.string().trim().min(1, 'Write a comment first.').max(4000),
});

export async function GET(_request: Request, ctx: RouteContext<'/api/w/[workspaceId]/questions/[questionId]/comments'>) {
  const { workspaceId, questionId } = await ctx.params;
  return query(({ user }) => listCommentThreads(user.id, workspaceId, questionId));
}

export async function POST(request: Request, ctx: RouteContext<'/api/w/[workspaceId]/questions/[questionId]/comments'>) {
  const { workspaceId, questionId } = await ctx.params;
  return mutation(async ({ user }) => createCommentThread(user.id, workspaceId, questionId, await parseJson(request, body)));
}
