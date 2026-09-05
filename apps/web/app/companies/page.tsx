import { CompanyManager } from "@/components/forms/company-manager";
import { AppShell } from "@/components/layout/app-shell";

export default function CompaniesPage() {
  return (
    <AppShell>
      <section className="pt-14">
        <p className="eyebrow">Company catalog</p>
        <h1 className="page-title">Review and save official employers.</h1>
        <p className="page-intro">
          Manual additions require your confirmation. Unsupported career sites
          remain saved but are never presented as actively monitored.
        </p>
        <CompanyManager />
      </section>
    </AppShell>
  );
}
