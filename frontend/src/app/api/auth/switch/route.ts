import { z } from 'zod';
import { supabaseForRequest } from '@/server/auth';
import { AppError } from '@/server/errors';
import { assertSameOrigin, errorResponse, parseJson } from '@/server/http';
import { env } from '@/server/env';
import { DEV_ACCOUNTS, DEV_PASSWORD, devSwitchEnabled } from '@/lib/dev-accounts';

const body = z.object({ email: z.string().trim().toLowerCase().email() });

/**
 * Development-only user switch for demos and testing. It performs a real sign-in with the
 * published development password, so it cannot reach any account outside DEV_ACCOUNTS, and it
 * does not exist unless APP_MODE is development and DEMO_USER_SWITCH is true.
 */
export async function POST(req: Request) {
  try {
    if (!devSwitchEnabled(env().APP_MODE, process.env.DEMO_USER_SWITCH)) throw new AppError('NOT_FOUND', 'Not found.');
    await assertSameOrigin();
    const { email } = await parseJson(req, body);
    if (!DEV_ACCOUNTS.some((a) => a.email === email)) throw new AppError('FORBIDDEN', 'Only the development accounts can be switched to.');
    const supabase = await supabaseForRequest();
    await supabase.auth.signOut({ scope: 'local' });
    const { error } = await supabase.auth.signInWithPassword({ email, password: DEV_PASSWORD });
    if (error) throw new AppError('UNAUTHENTICATED', 'That development account is not set up. Re-run the seed.');
    return Response.json({ ok: true, data: { email } });
  } catch (err) {
    return errorResponse(err);
  }
}
