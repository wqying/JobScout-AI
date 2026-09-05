import { ArrowRight, BellRing, Building2, Search } from "lucide-react";
import Link from "next/link";

import { AppHeader } from "@/components/layout/app-shell";

const capabilities = [
  {
    icon: Search,
    title: "Research employers",
    body: "Discover US companies in an industry with visible, sourced evidence.",
  },
  {
    icon: Building2,
    title: "Monitor official sources",
    body: "Track supported careers pages while this local installation is running.",
  },
  {
    icon: BellRing,
    title: "Receive focused alerts",
    body: "Get deduplicated notifications for roles matching your own preferences.",
  },
];

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-6xl flex-col px-6 py-8 sm:px-10 lg:px-16">
      <AppHeader />

      <section className="grid flex-1 items-center gap-12 py-20 lg:grid-cols-[1.35fr_0.65fr]">
        <div>
          <p className="mb-5 text-sm font-medium uppercase tracking-[0.18em] text-cyan-300">
            Private by design · Local by default
          </p>
          <h1 className="max-w-4xl text-balance text-5xl font-semibold leading-[1.02] tracking-[-0.04em] text-white sm:text-7xl">
            Find the employers worth watching.
          </h1>
          <p className="mt-7 max-w-2xl text-lg leading-8 text-slate-300">
            JobScout helps international students research employer evidence,
            monitor official job sources, and understand exactly why an opening
            matched.
          </p>
          <div className="mt-9 flex flex-wrap items-center gap-4">
            <Link className="button-primary" href="/discover">
              Discover companies <ArrowRight aria-hidden="true" size={18} />
            </Link>
            <span className="text-sm text-slate-400">
              Sourced research with local caching and visible scores
            </span>
          </div>
        </div>

        <div className="grid gap-4">
          {capabilities.map(({ icon: Icon, title, body }) => (
            <article
              className="rounded-2xl border border-white/10 bg-white/[0.04] p-6 shadow-2xl shadow-black/10"
              key={title}
            >
              <Icon
                aria-hidden="true"
                className="mb-5 text-cyan-300"
                size={24}
              />
              <h2 className="font-semibold text-white">{title}</h2>
              <p className="mt-2 text-sm leading-6 text-slate-400">{body}</p>
            </article>
          ))}
        </div>
      </section>

      <footer className="border-t border-white/10 pt-6 text-sm leading-6 text-slate-500">
        Informational only—not legal or immigration advice. Monitoring requires
        this laptop and the local services to remain online.
      </footer>
    </main>
  );
}
