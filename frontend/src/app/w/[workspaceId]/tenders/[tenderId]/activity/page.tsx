import { redirect } from 'next/navigation';
export default async function Page({ params }: PageProps<'/w/[workspaceId]/tenders/[tenderId]/activity'>) {
  const { workspaceId, tenderId } = await params;
  redirect(`/w/${workspaceId}/tenders/${tenderId}`);
}
