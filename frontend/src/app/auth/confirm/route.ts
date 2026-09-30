import { NextResponse, type NextRequest } from 'next/server';
import { supabaseForRequest } from '@/server/auth';
import { acceptPendingInvitations } from '@/server/services/workspaces';
import { env } from '@/server/env';

// Completes invite and password-recovery links: verifies the one-time token,
// signs the user in, accepts any open workspace invitations for their email,
// then asks them to choose a password.
export async function GET(request: NextRequest) {
  const tokenHash = request.nextUrl.searchParams.get('token_hash');
  const type = request.nextUrl.searchParams.get('type');
  // Redirect to the configured public origin. request.url carries the address the server
  // listens on (http://0.0.0.0:3000 in the container), where the new session cookie is not sent.
  const redirect = (path: string) => NextResponse.redirect(new URL(path, env().APP_URL));
  if (!tokenHash || (type !== 'invite' && type !== 'recovery')) {
    return redirect('/login?error=invalid_link');
  }
  const supabase = await supabaseForRequest();
  const { data, error } = await supabase.auth.verifyOtp({ type, token_hash: tokenHash });
  if (error || !data.user) {
    return redirect('/login?error=expired_link');
  }
  if (data.user.email) {
    await acceptPendingInvitations(data.user.id, data.user.email);
  }
  return redirect(`/auth/set-password?reason=${type}`);
}
