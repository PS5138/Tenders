import { Tenders } from '@/features/backend/tenders';
export default async function Page({ params }: PageProps<'/w/[workspaceId]/tenders'>) {
  const { workspaceId } = await params;
  return <Tenders workspaceId={workspaceId} />;
}
