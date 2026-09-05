import { DiscoveryResults } from "@/components/discovery/discovery-results";
import { AppShell } from "@/components/layout/app-shell";

export default function DiscoveryRunPage() {
  return (
    <AppShell>
      <section className="pt-14">
        <p className="eyebrow">Research results</p>
        <h1 className="page-title">Review sourced employer recommendations.</h1>
        <DiscoveryResults />
      </section>
    </AppShell>
  );
}
