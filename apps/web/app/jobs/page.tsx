import { JobList } from "@/components/jobs/job-list";
import { AppShell } from "@/components/layout/app-shell";

export default function JobsPage() {
  return (
    <AppShell>
      <section className="pt-14">
        <p className="eyebrow">Lifecycle history</p>
        <h1 className="page-title">Jobs from saved companies.</h1>
        <p className="page-intro">
          Current and closed postings collected from supported official career
          sources. JobScout keeps changes auditable without treating a failed or
          partial poll as proof that a job closed.
        </p>
        <div className="mt-10">
          <JobList />
        </div>
      </section>
    </AppShell>
  );
}
