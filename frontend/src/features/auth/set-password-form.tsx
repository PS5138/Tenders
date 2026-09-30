'use client';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Field, Input } from '@/components/ui/form';
import { api, errorMessage } from '@/lib/api-client';

export function SetPasswordForm({ askName }: { askName: boolean }) {
  const router = useRouter();
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mismatch = confirm.length > 0 && confirm !== password;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (password !== confirm) return;
    setBusy(true);
    setError(null);
    try {
      if (askName && name.trim()) await api('/api/account/profile', { method: 'PATCH', body: { displayName: name } });
      await api('/api/auth/password', { body: { password } });
      router.replace('/');
      router.refresh();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="mt-5 flex flex-col gap-4">
      {askName ? (
        <Field label="Your name" htmlFor="name" hint="Shown to colleagues on reviews and comments.">
          <Input id="name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
      ) : null}
      <Field label="New password" htmlFor="password">
        <Input id="password" type="password" autoComplete="new-password" minLength={10} required value={password} onChange={(e) => setPassword(e.target.value)} />
      </Field>
      <Field label="Confirm password" htmlFor="confirm" error={mismatch ? 'The passwords do not match.' : null}>
        <Input id="confirm" type="password" autoComplete="new-password" required aria-invalid={mismatch} value={confirm} onChange={(e) => setConfirm(e.target.value)} />
      </Field>
      {error ? (
        <p role="alert" className="text-xs text-red">
          {error}
        </p>
      ) : null}
      <Button type="submit" variant="primary" busy={busy} disabled={password.length < 10 || password !== confirm}>
        Save password and continue
      </Button>
    </form>
  );
}
