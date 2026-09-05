import { DiscoverySearch } from "@/components/discovery/discovery-search";
import { AppShell } from "@/components/layout/app-shell";

export default function DiscoverPage() {
  return (
    <AppShell>
      <section className="pt-14">
        <p className="eyebrow">Company discovery</p>
        <h1 className="page-title">Find US employers worth following.</h1>
        <p className="page-intro">
          Enter an industry and JobScout will research up to 20 companies,
          validate its sources, and rank the results with a reproducible score.
        </p>
        <DiscoverySearch />
        <p className="mt-6 max-w-3xl text-sm leading-6 text-slate-500">
          Ranked recommendations depend on available public evidence. Results
          are not exhaustive, and historical H-1B sponsorship activity does not
          guarantee sponsorship for a specific position.
        </p>
      </section>
    </AppShell>
  );
}
