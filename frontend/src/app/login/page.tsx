import type { Metadata } from 'next';
import { redirect } from 'next/navigation';
import { getSessionUser } from '@/server/auth';
import { LoginForm } from '@/features/auth/login-form';
import { Brand } from '@/features/shell/brand';

export const metadata: Metadata = { title: 'Sign in' };

const LINK_ERRORS: Record<string, string> = {
  invalid_link: 'That link is not valid. Ask your workspace admin for a new one.',
  expired_link: 'That link has expired or was already used. Ask your workspace admin for a new one.',
};

export default async function LoginPage({ searchParams }: PageProps<'/login'>) {
  const params = await searchParams;
  const next = typeof params.next === 'string' && params.next.startsWith('/') && !params.next.startsWith('//') ? params.next : '/';
  if (await getSessionUser()) redirect(next);
  const linkError = typeof params.error === 'string' ? LINK_ERRORS[params.error] : undefined;
  return (
    <main className="flex min-h-screen items-center justify-center bg-nav px-4 py-10">
      <div className="w-full max-w-sm rounded-xl border border-line bg-bg p-6 shadow-sm">
        <Brand />
        <h1 className="mt-6 text-xl font-semibold tracking-tight">Sign in</h1>
        <p className="mt-1 text-xs text-muted">Ten accounts are created by invitation from your workspace admin.</p>
        <LoginForm next={next} linkError={linkError} />
      </div>
    </main>
  );
}
