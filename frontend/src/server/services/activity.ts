import 'server-only';
import type { Tx } from '../db';

export type ActivityInput = {
  workspaceId: string;
  tenderId?: string | null;
  actorId?: string | null;
  type: string;
  objectType: string;
  objectId?: string | null;
  objectVersion?: string | number | null;
  summary: string;
  details?: Record<string, unknown>;
};

export async function logActivity(tx: Tx, a: ActivityInput): Promise<void> {
  await tx`
    insert into app.activity_events (workspace_id, tender_id, actor_id, type, object_type, object_id, object_version, summary, details)
    values (${a.workspaceId}, ${a.tenderId ?? null}, ${a.actorId ?? null}, ${a.type}, ${a.objectType}, ${a.objectId ?? null},
            ${a.objectVersion == null ? null : String(a.objectVersion)}, ${a.summary}, ${tx.json((a.details ?? {}) as never)})`;
}

export type NotificationInput = {
  workspaceId: string;
  recipientId: string;
  actorId?: string | null;
  eventType:
    | 'review_requested'
    | 'mentioned'
    | 'changes_requested'
    | 'review_completed'
    | 'review_superseded'
    | 'review_reassigned'
    | 'job_failed'
    | 'recheck_required'
    | 'draft_ready';
  objectType: string;
  objectId: string;
  tenderId?: string | null;
  responseId?: string | null;
  summary: string;
  idempotencyKey?: string | null;
};

/** Creates an in-app notification. Repeating the same idempotency key is a no-op. */
export async function notify(tx: Tx, n: NotificationInput): Promise<void> {
  if (n.actorId && n.actorId === n.recipientId && n.eventType !== 'job_failed') return;
  await tx`
    select app.create_notification(${n.workspaceId}, ${n.recipientId}, ${n.actorId ?? null}, ${n.eventType}, ${n.objectType}, ${n.objectId},
                                   ${n.tenderId ?? null}, ${n.responseId ?? null}, ${n.summary}, ${n.idempotencyKey ?? null})`;
}
