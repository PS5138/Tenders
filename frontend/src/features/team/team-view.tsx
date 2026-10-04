'use client';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Copy, Link2, UserPlus } from 'lucide-react';
import { Avatar } from '@/components/ui/avatar';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Field, Input, Select } from '@/components/ui/form';
import { Notice } from '@/components/ui/notice';
import { api, errorMessage } from '@/lib/api-client';
import { formatDate } from '@/lib/format';

type Member = {
  userId: string;
  displayName: string;
  email: string;
  role: 'admin' | 'member';
  jobTitle: string | null;
  deactivatedAt: string | null;
  openReviews: number;
};
type Invitation = {
  id: string;
  email: string;
  role: string;
  jobTitle: string | null;
  createdAt: string;
  expiresAt: string;
  invitedByName: string | null;
};

export function TeamView({
  workspaceId,
  me,
  members,
  invitations,
}: {
  workspaceId: string;
  me: { userId: string; role: string };
  members: Member[];
  invitations: Invitation[];
}) {
  const router = useRouter();
  const isAdmin = me.role === 'admin';
  const [inviting, setInviting] = useState(false);
  const [form, setForm] = useState({ email: '', displayName: '', role: 'member', jobTitle: '' });
  const [link, setLink] = useState<{ email: string; url: string } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [removing, setRemoving] = useState<Member | null>(null);

  async function run<T>(key: string, body: Record<string, unknown>): Promise<T | null> {
    setBusy(key);
    setError(null);
    setMessage(null);
    try {
      const res = await api<T>(`/api/w/${workspaceId}/team`, { body });
      router.refresh();
      return res;
    } catch (err) {
      setError(errorMessage(err));
      return null;
    } finally {
      setBusy(null);
    }
  }

  const active = members.filter((m) => !m.deactivatedAt);
  const former = members.filter((m) => m.deactivatedAt);

  return (
    <div className="space-y-6">
      {error ? <Notice tone="red">{error}</Notice> : null}
      {message ? <Notice tone="green">{message}</Notice> : null}
      {link ? (
        <Notice
          tone="blue"
          title={`Invitation link for ${link.email}`}
          action={
            <Button size="sm" onClick={() => navigator.clipboard?.writeText(link.url).then(() => setMessage('Link copied.'))}>
              <Copy aria-hidden /> Copy
            </Button>
          }
        >
          <p className="break-all font-mono text-[11px]">{link.url}</p>
          <p className="mt-1">
            Share this one-time link with them directly. It is shown only now, expires in 7 days, and lets them choose a password and join
            this workspace. No email is sent by Ten.
          </p>
        </Notice>
      ) : null}

      <section>
        <div className="mb-2 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">Members ({active.length})</h2>
          {isAdmin ? (
            <Button variant="primary" onClick={() => setInviting(true)}>
              <UserPlus aria-hidden /> Add a person
            </Button>
          ) : null}
        </div>
        <ul className="divide-y divide-line border-y border-line">
          {active.map((m) => (
            <li key={m.userId} className="flex flex-wrap items-center justify-between gap-3 py-3.5">
              <div className="flex min-w-0 items-center gap-3">
                <Avatar name={m.displayName} />
                <div className="min-w-0">
                  <p className="text-[13px] font-semibold wrap-anywhere">
                    {m.displayName} {m.userId === me.userId ? <span className="font-normal text-muted">(you)</span> : null}
                  </p>
                  <p className="text-xs text-muted wrap-anywhere">
                    {m.email}
                    {m.jobTitle ? ` · ${m.jobTitle}` : ''}
                  </p>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2 text-xs">
                {m.openReviews ? (
                  <Badge tone="blue">
                    {m.openReviews} open review{m.openReviews === 1 ? '' : 's'}
                  </Badge>
                ) : null}
                {isAdmin ? (
                  <>
                    <label>
                      <span className="sr-only">Role for {m.displayName}</span>
                      <Select
                        className="w-28 py-1 text-xs"
                        value={m.role}
                        disabled={busy === `role-${m.userId}`}
                        onChange={(e) => run(`role-${m.userId}`, { action: 'role', userId: m.userId, role: e.target.value })}
                      >
                        <option value="member">Member</option>
                        <option value="admin">Admin</option>
                      </Select>
                    </label>
                    {/* A fixed slot, empty on your own row, so every role dropdown lines up. */}
                    <span className="flex w-16 justify-end">
                      {m.userId !== me.userId ? (
                        <Button size="sm" variant="ghost" onClick={() => setRemoving(m)}>
                          Remove
                        </Button>
                      ) : null}
                    </span>
                  </>
                ) : (
                  <Badge tone={m.role === 'admin' ? 'green' : 'neutral'}>{m.role === 'admin' ? 'Admin' : 'Member'}</Badge>
                )}
              </div>
            </li>
          ))}
        </ul>
      </section>

      {isAdmin && invitations.length ? (
        <section>
          <h2 className="mb-2 text-sm font-semibold">Pending invitations</h2>
          <ul className="divide-y divide-line border-y border-line text-xs">
            {invitations.map((i) => (
              <li key={i.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
                <span className="wrap-anywhere">
                  <span className="font-medium">{i.email}</span> · {i.role} · invited by {i.invitedByName ?? 'unknown'} · expires{' '}
                  {formatDate(i.expiresAt)}
                </span>
                <span className="flex gap-1">
                  <Button
                    size="sm"
                    variant="ghost"
                    busy={busy === `regen-${i.id}`}
                    onClick={async () => {
                      const res = await run<{ link: string }>(`regen-${i.id}`, { action: 'regenerate', invitationId: i.id });
                      if (res) setLink({ email: i.email, url: res.link });
                    }}
                  >
                    <Link2 aria-hidden /> New link
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    busy={busy === `revoke-${i.id}`}
                    onClick={() => run(`revoke-${i.id}`, { action: 'revoke', invitationId: i.id })}
                  >
                    Revoke
                  </Button>
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {former.length ? (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted">
            {former.length} former member{former.length === 1 ? '' : 's'}
          </summary>
          <ul className="mt-2 space-y-1">
            {former.map((m) => (
              <li key={m.userId}>
                {m.displayName} ({m.email}) · removed {formatDate(m.deactivatedAt)}
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <Notice>
        Everyone in this business can collaborate on tenders. Any member can approve an answer once the backend evidence and gap checks
        pass.
      </Notice>

      <Dialog
        open={inviting}
        onOpenChange={setInviting}
        title="Add a person"
        description="People with an existing Ten account are added straight away. Anyone else gets a one-time invitation link for you to share."
      >
        <form
          className="space-y-3"
          onSubmit={async (e) => {
            e.preventDefault();
            const res = await run<{ kind: 'added_existing' } | { kind: 'invited'; link: string }>('invite', {
              action: 'invite',
              email: form.email,
              role: form.role,
              jobTitle: form.jobTitle || null,
              displayName: form.displayName || null,
            });
            if (res) {
              setInviting(false);
              if (res.kind === 'invited') setLink({ email: form.email, url: res.link });
              else setMessage(`${form.email} already had an account and was added to the workspace.`);
              setForm({ email: '', displayName: '', role: 'member', jobTitle: '' });
            }
          }}
        >
          <Field label="Email" htmlFor="inv-email">
            <Input id="inv-email" type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          </Field>
          <Field label="Name (for new accounts)" htmlFor="inv-name">
            <Input id="inv-name" value={form.displayName} onChange={(e) => setForm({ ...form, displayName: e.target.value })} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Role" htmlFor="inv-role">
              <Select id="inv-role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
                <option value="member">Member</option>
                <option value="admin">Admin</option>
              </Select>
            </Field>
            <Field label="Job title (optional)" htmlFor="inv-title">
              <Input id="inv-title" value={form.jobTitle} onChange={(e) => setForm({ ...form, jobTitle: e.target.value })} />
            </Field>
          </div>
          <div className="flex gap-2">
            <Button type="submit" variant="primary" busy={busy === 'invite'} disabled={!form.email}>
              Add
            </Button>
            <Button variant="ghost" onClick={() => setInviting(false)}>
              Cancel
            </Button>
          </div>
        </form>
      </Dialog>

      <Dialog
        open={Boolean(removing)}
        onOpenChange={(o) => !o && setRemoving(null)}
        title={`Remove ${removing?.displayName ?? ''}?`}
        description="They lose access to this workspace immediately. Their history and approvals are kept."
      >
        {removing?.openReviews ? (
          <Notice tone="amber" className="mb-3">
            They have {removing.openReviews} open review{removing.openReviews === 1 ? '' : 's'}. The team will need to reassign them.
          </Notice>
        ) : null}
        <div className="flex gap-2">
          <Button
            variant="danger"
            busy={busy === 'remove'}
            onClick={async () => {
              if (await run('remove', { action: 'remove', userId: removing!.userId })) setRemoving(null);
            }}
          >
            Remove from workspace
          </Button>
          <Button variant="ghost" onClick={() => setRemoving(null)}>
            Cancel
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
