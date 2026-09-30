import { z } from 'zod';
import { mutation, parseJson } from '@/server/http';
import { updateMyProfile } from '@/server/services/workspaces';

const body = z.object({ displayName: z.string().trim().min(1).max(100) });

export async function PATCH(req: Request) {
  return mutation(async ({ user }) => {
    const { displayName } = await parseJson(req, body);
    return updateMyProfile(user.id, displayName);
  });
}
