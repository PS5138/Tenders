import { redirect } from 'next/navigation';

/** My reviews became My tasks; old links and notifications still land in the right place. */
export default async function Page({ params }: PageProps<'/w/[workspaceId]/reviews'>) {
  const { workspaceId } = await params;
  redirect(`/w/${workspaceId}/tasks`);
}
