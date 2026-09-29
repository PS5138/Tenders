import { z } from 'zod';
import { supabaseForRequest } from '@/server/auth';
import { AppError } from '@/server/errors';
import { mutation, parseJson } from '@/server/http';

const body = z.object({ password: z.string().min(10, 'Use at least 10 characters.').max(200) });

export async function POST(req: Request) {
  return mutation(async () => {
    const { password } = await parseJson(req, body);
    const supabase = await supabaseForRequest();
    const { error } = await supabase.auth.updateUser({ password });
    if (error) throw new AppError('VALIDATION_FAILED', error.message);
    return { updated: true };
  });
}
