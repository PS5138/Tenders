import { LibraryDocument } from '@/features/backend/library';
export default async function Page({ params }: PageProps<'/w/[workspaceId]/library/[documentId]'>) {
  const { workspaceId, documentId } = await params;
  return <LibraryDocument workspaceId={workspaceId} documentId={documentId} />;
}
