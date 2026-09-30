import { supabaseForRequest } from '@/server/auth';
import { errorResponse } from '@/server/http';

export async function POST(req: Request) {
  try {
    if (req.headers.get('x-ten-request') !== '1') {
      return Response.json({ ok: false, error: { code: 'FORBIDDEN', message: 'Invalid request.' } }, { status: 403 });
    }
    const supabase = await supabaseForRequest();
    await supabase.auth.signOut({ scope: 'local' });
    return Response.json({ ok: true, data: { signedOut: true } });
  } catch (err) {
    return errorResponse(err);
  }
}
