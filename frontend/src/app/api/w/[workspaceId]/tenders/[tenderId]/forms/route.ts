import { z } from 'zod';
import { mutation, query, parseJson } from '@/server/http';
import { formLocations, saveFormLocation } from '@/server/backend/forms';
const schema = z.object({
  questionId: z.uuid(),
  documentId: z.uuid(),
  target: z.discriminatedUnion('kind', [
    z.object({ kind: z.literal('xlsx_cell'), sheet: z.string().min(1).max(100), ref: z.string().regex(/^[A-Z]{1,3}[1-9][0-9]{0,6}$/) }),
    z.object({
      kind: z.literal('docx_table_cell'),
      table: z.number().int().positive(),
      row: z.number().int().positive(),
      cell: z.number().int().positive(),
    }),
  ]),
});
export async function GET(_request: Request, ctx: RouteContext<'/api/w/[workspaceId]/tenders/[tenderId]/forms'>) {
  const { workspaceId, tenderId } = await ctx.params;
  return query(({ user }) => formLocations(user.id, workspaceId, tenderId));
}
export async function POST(request: Request, ctx: RouteContext<'/api/w/[workspaceId]/tenders/[tenderId]/forms'>) {
  const { workspaceId, tenderId } = await ctx.params;
  return mutation(async ({ user }) => saveFormLocation(user.id, workspaceId, tenderId, await parseJson(request, schema)));
}
