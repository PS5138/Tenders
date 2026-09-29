import { Library } from '@/features/backend/library';
export default async function Page({ params }: PageProps<'/w/[workspaceId]/library'>) {
  const { workspaceId } = await params;
  return <Library workspaceId={workspaceId} />;
}
