import 'server-only';
import { inWorkspace } from './context';
import { backendForWorkspace, BackendError } from '../backend/client';

export type NotificationItem = {
  id: string;
  eventType: string;
  summary: string;
  objectType: string;
  objectId: string;
  tenderId: string | null;
  responseId: string | null;
  actorName: string | null;
  readAt: Date | null;
  createdAt: Date;
};

export async function listNotifications(userId: string, workspaceId: string, opts: { limit?: number; unreadOnly?: boolean } = {}) {
  const limit = Math.min(Math.max(opts.limit ?? 30, 1), 200);
  const rows = await inWorkspace(userId, workspaceId, (tx) => tx<NotificationItem[]>`
    select n.id, n.event_type, n.summary, n.object_type, n.object_id, n.tender_id, n.response_id, n.read_at, n.created_at,
           p.display_name as actor_name
    from app.notifications n
    left join app.profiles p on p.user_id = n.actor_id
    where n.workspace_id = ${workspaceId} and n.recipient_id = ${userId}
      ${opts.unreadOnly ? tx`and n.read_at is null` : tx``}
    order by n.created_at desc
    limit ${limit}`);
  const client=await backendForWorkspace(userId,workspaceId);
  const ids=[...new Set(rows.map(row=>row.responseId).filter((id):id is string=>Boolean(id)))];
  const exists=new Set(await Promise.all(ids.map(async id=>{try{await client.get('/questions/{question_id}',{question_id:id});return id;}catch(error){if(error instanceof BackendError&&error.status===404)return null;throw error;}})));
  return rows.filter(row=>!row.responseId||exists.has(row.responseId));
}

/** Marks notifications read. Reading never resolves the review task behind it. */
export async function markNotificationsRead(userId: string, workspaceId: string, ids: string[] | 'all') {
  return inWorkspace(userId, workspaceId, async (tx) => {
    const res =
      ids === 'all'
        ? await tx`update app.notifications set read_at = now() where workspace_id = ${workspaceId} and recipient_id = ${userId} and read_at is null`
        : ids.length
          ? await tx`update app.notifications set read_at = now()
                     where workspace_id = ${workspaceId} and recipient_id = ${userId} and id in ${tx(ids)} and read_at is null`
          : { count: 0 };
    return { marked: res.count };
  });
}
