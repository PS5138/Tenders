import { notFound } from 'next/navigation';
import { backendForWorkspace, BackendError } from '@/server/backend/client';
import { QuestionWorkspace } from '@/features/backend/question';
import { requireUser } from '@/server/auth';
import { listActiveMembers } from '@/server/services/workspaces';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default async function Page({ params, searchParams }: PageProps<'/w/[workspaceId]/tenders/[tenderId]/responses/[responseId]'>) {
  const { workspaceId, tenderId, responseId } = await params;
  const { thread } = await searchParams;
  const user = await requireUser();
  try {
    const client = await backendForWorkspace(user.id, workspaceId);
    const q = await client.get('/questions/{question_id}', { question_id: responseId });
    if (q.tender_id !== tenderId) notFound();
  } catch (error) {
    if (error instanceof BackendError && error.status === 404) notFound();
    throw error;
  }
  const members = await listActiveMembers(user.id, workspaceId);
  return (
    <QuestionWorkspace
      workspaceId={workspaceId}
      tenderId={tenderId}
      questionId={responseId}
      members={members}
      me={user.id}
      initialThread={typeof thread === 'string' && uuid.test(thread) ? thread : null}
    />
  );
}
