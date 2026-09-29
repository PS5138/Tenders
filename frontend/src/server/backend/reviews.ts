import 'server-only';
import { backendForWorkspace } from './client';

/** Unapproved questions across the business's open tenders, with their tender's name and deadline. */
export async function openQuestions(userId: string, workspaceId: string) {
  const client = await backendForWorkspace(userId, workspaceId);
  const tenders = await client.get('/tenders', {});
  const groups = await Promise.all(
    tenders
      .filter((t) => t.status === 'open')
      .map(async (t) => ({
        tender: t,
        questions: await client.get('/tenders/{tender_id}/questions', {
          tender_id: t.id,
        }),
      })),
  );
  return groups.flatMap((group) =>
    group.questions
      .filter((q) => q.status !== 'approved')
      .map((q) => ({
        ...q,
        tenderName: group.tender.name,
        tenderDeadline: group.tender.deadline ?? null,
        tenderDaysRemaining: group.tender.days_remaining ?? null,
      })),
  );
}
