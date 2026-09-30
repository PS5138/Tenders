import type { Metadata } from 'next';
import { redirect } from 'next/navigation';
import { getSessionUser } from '@/server/auth';
import { SetPasswordForm } from '@/features/auth/set-password-form';
import { Brand } from '@/features/shell/brand';

export const metadata: Metadata = { title: 'Choose a password' };

export default async function SetPasswordPage({ searchParams }: PageProps<'/auth/set-password'>) {
  const user = await getSessionUser();
  if (!user) redirect('/login?error=expired_link');
  const { reason } = await searchParams;
  return (
    <main className="flex min-h-screen items-center justify-center bg-nav px-4 py-10">
      <div className="w-full max-w-sm rounded-xl border border-line bg-bg p-6 shadow-sm">
        <Brand />
        <h1 className="mt-6 text-xl font-semibold tracking-tight">{reason === 'invite' ? 'Welcome to Ten' : 'Choose a new password'}</h1>
        <p className="mt-1 text-xs text-muted wrap-anywhere">Signed in as {user.email}. Choose a password of at least 10 characters.</p>
        <SetPasswordForm askName={reason === 'invite'} />
      </div>
    </main>
  );
}
