import { notFound } from 'next/navigation';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default async function Layout({ children, params }: LayoutProps<'/w/[workspaceId]/tenders/[tenderId]'>) {
  // Backend IDs are UUIDs; anything else is a mistyped or truncated link.
  if (!uuid.test((await params).tenderId)) notFound();
  return children;
}
