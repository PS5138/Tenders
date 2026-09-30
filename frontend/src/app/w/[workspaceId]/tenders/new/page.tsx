import { NewTender } from '@/features/backend/tenders';
export default async function Page({ params }: PageProps<'/w/[workspaceId]/tenders/new'>) {
  const { workspaceId } = await params;
  return <NewTender workspaceId={workspaceId} />;
}
