import { NextResponse, type NextRequest } from 'next/server';
import { createServerClient } from '@supabase/ssr';

// Pages reachable without a session. Everything else requires sign-in; there is
// no query parameter or persona switch that bypasses this.
const PUBLIC_PATHS = ['/login', '/auth/', '/api/auth/', '/api/health'];

function isPublic(pathname: string): boolean {
  return PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(p));
}

export async function proxy(request: NextRequest) {
  let response = NextResponse.next({ request });
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_ANON_KEY;
  if (!url || !key) {
    return new NextResponse('Ten is not configured: SUPABASE_URL and SUPABASE_ANON_KEY are required.', { status: 503 });
  }

  const supabase = createServerClient(url, key, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (toSet, headers) => {
        for (const { name, value } of toSet) request.cookies.set(name, value);
        response = NextResponse.next({ request });
        for (const { name, value, options } of toSet) response.cookies.set(name, value, options);
        for (const [k, v] of Object.entries(headers ?? {})) response.headers.set(k, v);
      },
    },
  });

  // Refreshes an expiring session and writes the new cookies onto the response.
  const { data } = await supabase.auth.getClaims();
  const signedIn = Boolean(data?.claims?.sub);
  const { pathname, search } = request.nextUrl;

  if (!signedIn && !isPublic(pathname)) {
    if (pathname.startsWith('/api/')) {
      return NextResponse.json(
        { ok: false, error: { code: 'UNAUTHENTICATED', message: 'Please sign in to continue.' } },
        { status: 401 },
      );
    }
    const login = request.nextUrl.clone();
    login.pathname = '/login';
    login.search = pathname === '/' ? '' : `?next=${encodeURIComponent(pathname + search)}`;
    return NextResponse.redirect(login);
  }

  response.headers.set('cache-control', 'private, no-store');
  return response;
}

export const config = {
  // Upload requests skip the proxy so their bodies are never buffered or
  // truncated; the upload route authenticates itself.
  matcher: ['/((?!_next/static|_next/image|favicon.ico|icon.svg|api/w/[^/]+/(?:uploads|backend)(?:/|$)).*)'],
};
