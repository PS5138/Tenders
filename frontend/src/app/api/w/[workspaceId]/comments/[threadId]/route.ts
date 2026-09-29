import { z } from 'zod';
import { mutation, parseJson, uuid } from '@/server/http';
import { deleteCommentMessage, replyToThread, setThreadResolved } from '@/server/services/collaboration';

const body = z.discriminatedUnion('action', [
  z.object({ action: z.literal('reply'), body: z.string().trim().min(1, 'Write a reply first.').max(4000) }),
  z.object({ action: z.literal('resolve') }),
  z.object({ action: z.literal('reopen') }),
  z.object({ action: z.literal('delete_message'), messageId: uuid }),
]);

export async function POST(request: Request, ctx: RouteContext<'/api/w/[workspaceId]/comments/[threadId]'>) {
  const { workspaceId, threadId } = await ctx.params;
  return mutation(async ({ user }) => {
    const input = await parseJson(request, body);
    switch (input.action) {
      case 'reply':
        return replyToThread(user.id, workspaceId, threadId, input.body);
      case 'resolve':
      case 'reopen':
        return setThreadResolved(user.id, workspaceId, threadId, input.action === 'resolve');
      case 'delete_message':
        return deleteCommentMessage(user.id, workspaceId, input.messageId);
    }
  });
}
