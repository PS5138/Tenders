'use client';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Field, Input } from '@/components/ui/form';
import { Notice } from '@/components/ui/notice';
import { api, errorMessage } from '@/lib/api-client';

export function LoginForm({ next, linkError }: { next: string; linkError?: string }) {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api('/api/auth/login', { body: { email, password } });
      router.replace(next);
      router.refresh();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="mt-5 flex flex-col gap-4" noValidate>
      {linkError ? <Notice tone="amber">{linkError}</Notice> : null}
      <Field label="Email" htmlFor="email">
        <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
      </Field>
      <Field label="Password" htmlFor="password">
        <Input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
      </Field>
      {error ? (
        <p role="alert" className="text-xs text-red">
          {error}
        </p>
      ) : null}
      <Button type="submit" variant="primary" busy={busy} disabled={!email || !password}>
        Sign in
      </Button>
      <p className="text-xs text-muted">Forgotten your password? Ask a workspace admin to arrange a reset; self-service reset emails need the email service configured for this environment.</p>
    </form>
  );
}
