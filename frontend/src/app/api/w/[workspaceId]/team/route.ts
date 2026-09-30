import { z } from 'zod';
import { mutation, parseJson, uuid } from '@/server/http';
import {
  changeMemberRole,
  deactivateMember,
  inviteMember,
  regenerateInvitationLink,
  revokeInvitation,
  updateMemberTitle,
} from '@/server/services/workspaces';

const action = z.discriminatedUnion('action', [
  z.object({
    action: z.literal('invite'),
    email: z.string().trim().toLowerCase().email(),
    role: z.enum(['admin', 'member']),
    jobTitle: z.string().trim().max(120).nullable().optional(),
    displayName: z.string().trim().max(100).nullable().optional(),
  }),
  z.object({ action: z.literal('role'), userId: uuid, role: z.enum(['admin', 'member']) }),
  z.object({ action: z.literal('title'), userId: uuid, jobTitle: z.string().trim().max(120).nullable() }),
  z.object({ action: z.literal('remove'), userId: uuid }),
  z.object({ action: z.literal('regenerate'), invitationId: uuid }),
  z.object({ action: z.literal('revoke'), invitationId: uuid }),
]);

export async function POST(req: Request, ctx: RouteContext<'/api/w/[workspaceId]/team'>) {
  const { workspaceId } = await ctx.params;
  return mutation(async ({ user }) => {
    const a = await parseJson(req, action);
    switch (a.action) {
      case 'invite':
        return inviteMember(user.id, workspaceId, {
          email: a.email,
          role: a.role,
          jobTitle: a.jobTitle ?? null,
          displayName: a.displayName ?? null,
        });
      case 'role':
        return changeMemberRole(user.id, workspaceId, a.userId, a.role);
      case 'title':
        return updateMemberTitle(user.id, workspaceId, a.userId, a.jobTitle || null);
      case 'remove':
        return deactivateMember(user.id, workspaceId, a.userId);
      case 'regenerate':
        return regenerateInvitationLink(user.id, workspaceId, a.invitationId);
      case 'revoke':
        return revokeInvitation(user.id, workspaceId, a.invitationId);
    }
  });
}
