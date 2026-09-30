import { z } from 'zod';
import { supabaseForRequest } from '@/server/auth';
import { AppError } from '@/server/errors';
import { errorResponse, parseJson } from '@/server/http';

const body = z.object({ email: z.string().trim().toLowerCase().email(), password: z.string().min(1).max(200) });

export async function POST(req: Request) {
  try {
    if (req.headers.get('x-ten-request') !== '1') throw new AppError('FORBIDDEN', 'This request must come from the Ten application.');
    const { email, password } = await parseJson(req, body);
    const supabase = await supabaseForRequest();
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) {
      const limited = error.status === 429;
      throw new AppError(
        limited ? 'RATE_LIMITED' : 'UNAUTHENTICATED',
        limited ? 'Too many sign-in attempts. Wait a minute and try again.' : 'That email and password do not match an account.',
      );
    }
    return Response.json({ ok: true, data: { signedIn: true } });
  } catch (err) {
    return errorResponse(err);
  }
}
