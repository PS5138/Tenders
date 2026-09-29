import { z } from 'zod';
import { errorResponse, parseJson, assertSameOrigin } from '@/server/http';
import { requireUser } from '@/server/auth';
import { filledForm } from '@/server/backend/forms';
import { BackendError } from '@/server/backend/client';
export async function POST(request: Request, ctx: RouteContext<'/api/w/[workspaceId]/tenders/[tenderId]/forms/export'>) {
  try {
    await assertSameOrigin();
    const user = await requireUser();
    const { workspaceId, tenderId } = await ctx.params;
    const { documentId } = await parseJson(request, z.object({ documentId: z.uuid() }));
    const result = await filledForm(user.id, workspaceId, tenderId, documentId);
    return new Response(Buffer.from(result.bytes), {
      headers: {
        'content-type': 'application/octet-stream',
        'content-disposition': `attachment; filename*=UTF-8''${encodeURIComponent(result.filename)}`,
        'cache-control': 'private, no-store',
        'x-filled-answers': String(result.applied),
      },
    });
  } catch (e) {
    if (e instanceof BackendError) return Response.json(e.body, { status: e.status });
    return errorResponse(e);
  }
}
