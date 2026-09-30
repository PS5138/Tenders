import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';
import { ArrowLeft } from 'lucide-react';
import { getSessionUser } from '@/server/auth';
import { getMyProfile, listMyWorkspaces } from '@/server/services/workspaces';
import { Brand } from '@/features/shell/brand';
import { AccountForms } from '@/features/auth/account-forms';

export const metadata: Metadata = { title: 'Account settings' };

export default async function AccountPage({ searchParams }: PageProps<'/account'>) {
  const user = await getSessionUser();
  if (!user) redirect('/login?next=/account');
  const { ws } = await searchParams;
  const [profile, workspaces] = await Promise.all([getMyProfile(user.id), listMyWorkspaces(user.id)]);
  const back = workspaces.find((w) => w.id === ws) ?? workspaces[0];
  return (
    <main className="mx-auto max-w-xl px-4 py-8">
      <Brand />
      {back ? (
        <Link href={`/w/${back.id}/tenders`} className="mt-6 inline-flex items-center gap-1 text-xs text-muted hover:text-ink">
          <ArrowLeft className="size-3.5" aria-hidden /> Back to {back.name}
        </Link>
      ) : null}
      <h1 className="mt-3 text-2xl font-semibold tracking-tight">Account settings</h1>
      <p className="mt-1 text-sm text-muted">Signed in as {user.email}.</p>
      <AccountForms displayName={profile?.displayName ?? ''} workspaces={workspaces} />
    </main>
  );
}
