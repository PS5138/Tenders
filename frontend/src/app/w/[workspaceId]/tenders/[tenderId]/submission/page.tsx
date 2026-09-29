import { BuyerForms } from '@/features/backend/forms';
export default async function Page({ params }: PageProps<'/w/[workspaceId]/tenders/[tenderId]/submission'>) {
  return <BuyerForms {...await params} />;
}
