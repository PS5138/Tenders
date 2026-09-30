import { SignOutButton } from '@/features/auth/sign-out-button';
import { Brand } from '@/features/shell/brand';

export function NoWorkspace({ email }: { email: string }) {
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-4 px-4">
      <Brand />
      <h1 className="text-xl font-semibold">You are not in a workspace yet</h1>
      <p className="text-sm text-muted">
        You are signed in as {email}. Ask an admin of your business workspace to add you from its Team page. Once they do, sign in again.
      </p>
      <div>
        <SignOutButton />
      </div>
    </main>
  );
}
