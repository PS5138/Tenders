import { redirect } from 'next/navigation';
export default async function Page({ params }: PageProps<'/w/[workspaceId]/tenders/[tenderId]/documents'>) {
  const { workspaceId, tenderId } = await params;
  redirect(`/w/${workspaceId}/tenders/${tenderId}`);
}
