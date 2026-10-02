import { DiscoverySearch } from "@/components/discovery/discovery-search";
import { AppShell } from "@/components/layout/app-shell";

export default function DiscoverPage() {
  return (
    <AppShell>
      <section className="pt-14">
        <p className="eyebrow">Company discovery</p>
        <h1 className="page-title">Find US employers worth following.</h1>
        <p className="page-intro">
          Enter an industry and JobScout will research up to 40 company
          suggestions, check usable careers sources, and show the results
          alphabetically.
        </p>
        <DiscoverySearch />
        <p className="mt-6 max-w-3xl text-sm leading-6 text-slate-500">
          AI suggestions may be incomplete or wrong. Save and confirm a company
          before treating it as verified in this installation. Historical H-1B
          activity does not guarantee sponsorship for a specific position.
        </p>
      </section>
    </AppShell>
  );
}
