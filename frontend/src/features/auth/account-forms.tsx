'use client';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Field, Input } from '@/components/ui/form';
import { Panel } from '@/components/ui/page-header';
import { api, errorMessage } from '@/lib/api-client';
import { SignOutButton } from './sign-out-button';

export function AccountForms({ displayName, workspaces }: { displayName: string; workspaces: Array<{ id: string; name: string; role: string }> }) {
  const router = useRouter();
  const [name, setName] = useState(displayName);
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [msg, setMsg] = useState<{ key: string; text: string; ok: boolean } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function run(key: string, fn: () => Promise<unknown>, success: string) {
    setBusy(key);
    setMsg(null);
    try {
      await fn();
      setMsg({ key, text: success, ok: true });
      router.refresh();
    } catch (err) {
      setMsg({ key, text: errorMessage(err), ok: false });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mt-6 space-y-5">
      <Panel>
        <h2 className="mb-3 text-sm font-semibold">Profile</h2>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            void run('name', () => api('/api/account/profile', { method: 'PATCH', body: { displayName: name } }), 'Name saved.');
          }}
        >
          <Field label="Name shown to colleagues" htmlFor="acc-name">
            <Input id="acc-name" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Button type="submit" busy={busy === 'name'} disabled={!name.trim() || name === displayName}>
            Save name
          </Button>
          {msg?.key === 'name' ? <p className={`text-xs ${msg.ok ? 'text-accent' : 'text-red'}`}>{msg.text}</p> : null}
        </form>
      </Panel>
      <Panel>
        <h2 className="mb-3 text-sm font-semibold">Password</h2>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            void run(
              'password',
              async () => {
                await api('/api/auth/password', { body: { password } });
                setPassword('');
                setConfirm('');
              },
              'Password changed.',
            );
          }}
        >
          <Field label="New password (at least 10 characters)" htmlFor="acc-pw">
            <Input id="acc-pw" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <Field label="Confirm new password" htmlFor="acc-pw2" error={confirm && confirm !== password ? 'The passwords do not match.' : null}>
            <Input id="acc-pw2" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </Field>
          <Button type="submit" busy={busy === 'password'} disabled={password.length < 10 || password !== confirm}>
            Change password
          </Button>
          {msg?.key === 'password' ? <p className={`text-xs ${msg.ok ? 'text-accent' : 'text-red'}`}>{msg.text}</p> : null}
        </form>
      </Panel>
      <Panel>
        <h2 className="mb-3 text-sm font-semibold">Workspaces</h2>
        <ul className="space-y-1.5 text-sm">
          {workspaces.map((w) => (
            <li key={w.id} className="flex items-center justify-between gap-2">
              <span className="wrap-anywhere">{w.name}</span>
              <Badge tone={w.role === 'admin' ? 'green' : 'neutral'}>{w.role === 'admin' ? 'Admin' : 'Member'}</Badge>
            </li>
          ))}
        </ul>
      </Panel>
      <SignOutButton />
    </div>
  );
}
