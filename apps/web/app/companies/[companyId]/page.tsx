import { CompanyDetailView } from "@/components/jobs/company-detail";
import { AppShell } from "@/components/layout/app-shell";

export default async function CompanyDetailPage({
  params,
}: {
  params: Promise<{ companyId: string }>;
}) {
  const { companyId } = await params;
  return (
    <AppShell>
      <CompanyDetailView companyId={companyId} />
    </AppShell>
  );
}
