'use client';
import { useCallback, useEffect, useState } from 'react';
import { X } from 'lucide-react';
import { api } from '@/lib/api-client';
import { REVIEWER_ROLES, REVIEWER_ROLE_LABELS, type ReviewerRole } from '@/lib/review-roles';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ErrorNote, Field, field } from './shared';

type Member = { userId: string; displayName: string };
type Reviewer = { userId: string; displayName: string; role: ReviewerRole };

/** Owner (stored on the backend question) and reviewers (stored alongside it in Ten). */
export function People({
  workspaceId,
  questionId,
  members,
  owner,
  busy,
  onOwner,
}: {
  workspaceId: string;
  questionId: string;
  members: Member[];
  owner: string | null;
  busy: boolean;
  onOwner: (name: string | null) => void;
}) {
  const endpoint = `/api/w/${workspaceId}/questions/${questionId}/reviewers`;
  const [reviewers, setReviewers] = useState<Reviewer[]>([]),
    [person, setPerson] = useState(''),
    [role, setRole] = useState<ReviewerRole>('sme'),
    [saving, setSaving] = useState(false),
    [error, setError] = useState<string | null>(null);
  const reload = useCallback(async () => setReviewers(await api<Reviewer[]>(endpoint, { method: 'GET' })), [endpoint]);
  useEffect(() => {
    let active = true;
    api<Reviewer[]>(endpoint, { method: 'GET' })
      .then((rows) => {
        if (active) setReviewers(rows);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [endpoint]);
  async function change(action: 'add' | 'remove', userId: string, r: ReviewerRole) {
    setSaving(true);
    setError(null);
    try {
      await api(endpoint, { body: { action, userId, role: r } });
      await reload();
      if (action === 'add') setPerson('');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="space-y-4">
      <Field title="Owner">
        <select className={field} value={owner ?? ''} disabled={busy} onChange={(e) => onOwner(e.target.value || null)}>
          <option value="">No owner</option>
          {members.map((m) => (
            <option key={m.userId} value={m.displayName}>
              {m.displayName}
            </option>
          ))}
        </select>
      </Field>
      <div className="space-y-2">
        <h3 className="text-xs text-muted">Reviewers</h3>
        {reviewers.length ? (
          <ul className="space-y-1.5">
            {reviewers.map((r) => (
              <li key={`${r.userId}-${r.role}`} className="flex items-center justify-between gap-2 text-sm">
                <span className="flex min-w-0 items-center gap-2">
                  <span className="wrap-anywhere">{r.displayName}</span>
                  <Badge tone="violet">{REVIEWER_ROLE_LABELS[r.role]}</Badge>
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`Remove ${r.displayName} as ${REVIEWER_ROLE_LABELS[r.role]}`}
                  disabled={saving}
                  onClick={() => void change('remove', r.userId, r.role)}
                >
                  <X aria-hidden />
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted">No reviewers yet. Reviewers are notified and see the question in My tasks when it is their turn.</p>
        )}
        <div className="grid grid-cols-[1fr_auto] gap-2">
          <select className={field} aria-label="Reviewer" value={person} onChange={(e) => setPerson(e.target.value)}>
            <option value="">Choose a person</option>
            {members.map((m) => (
              <option key={m.userId} value={m.userId}>
                {m.displayName}
              </option>
            ))}
          </select>
          <select className={field} aria-label="Reviewer role" value={role} onChange={(e) => setRole(e.target.value as ReviewerRole)}>
            {REVIEWER_ROLES.map((r) => (
              <option key={r} value={r}>
                {REVIEWER_ROLE_LABELS[r]}
              </option>
            ))}
          </select>
        </div>
        <Button size="sm" busy={saving} disabled={!person} onClick={() => void change('add', person, role)}>
          Add reviewer
        </Button>
        <ErrorNote error={error} />
      </div>
    </div>
  );
}
