import 'server-only';
import { cache } from 'react';
import { cookies } from 'next/headers';
import { createServerClient } from '@supabase/ssr';
import { createClient, type SupabaseClient } from '@supabase/supabase-js';
import { env } from './env';
import { AppError } from './errors';

export type SessionUser = { id: string; email: string };

/** Supabase client bound to the current request's auth cookies. */
export async function supabaseForRequest(): Promise<SupabaseClient> {
  const cookieStore = await cookies();
  const config = env();
  return createServerClient(config.SUPABASE_URL, config.SUPABASE_ANON_KEY, {
    cookies: {
      getAll: () => cookieStore.getAll(),
      setAll: (toSet) => {
        try {
          for (const { name, value, options } of toSet) cookieStore.set(name, value, options);
        } catch {
          // Server Components cannot set cookies; proxy.ts refreshes sessions instead.
        }
      },
    },
  });
}

/** Service-role Supabase client for Auth admin calls and Storage. Server only. */
export function supabaseAdmin(): SupabaseClient {
  const config = env();
  return createClient(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY, {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
  });
}

/** The verified signed-in user for this request, or null. Cached per request. */
export const getSessionUser = cache(async (): Promise<SessionUser | null> => {
  const supabase = await supabaseForRequest();
  const { data, error } = await supabase.auth.getClaims();
  if (error || !data?.claims?.sub) return null;
  return { id: data.claims.sub, email: typeof data.claims.email === 'string' ? data.claims.email : '' };
});

export async function requireUser(): Promise<SessionUser> {
  const user = await getSessionUser();
  if (!user) throw new AppError('UNAUTHENTICATED', 'Please sign in to continue.');
  return user;
}
