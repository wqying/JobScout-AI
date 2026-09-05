import Link from "next/link";
import type { ReactNode } from "react";

import { ServiceStatus } from "@/components/service-status";

const navigation = [
  { href: "/discover", label: "Discover" },
  { href: "/companies", label: "Saved companies" },
  { href: "/jobs", label: "Jobs" },
  { href: "/alerts", label: "Alerts" },
  { href: "/settings", label: "Settings" },
];

export function AppHeader() {
  return (
    <header className="flex flex-wrap items-center justify-between gap-5 border-b border-white/10 pb-6">
      <Link
        className="text-sm font-semibold tracking-[0.2em] text-slate-200"
        href="/"
      >
        JOBSCOUT AI
      </Link>
      <nav
        aria-label="Primary navigation"
        className="flex flex-wrap items-center gap-x-5 gap-y-3 text-sm"
      >
        {navigation.map((item) => (
          <Link
            className="text-slate-300 transition hover:text-cyan-200"
            href={item.href}
            key={item.href}
          >
            {item.label}
          </Link>
        ))}
        <ServiceStatus />
      </nav>
    </header>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <main className="mx-auto min-h-screen max-w-6xl px-5 py-6 sm:px-10 lg:px-16">
      <AppHeader />
      {children}
      <footer className="mt-16 border-t border-white/10 py-7 text-sm leading-6 text-slate-500">
        Informational only—not legal or immigration advice. Historical H-1B
        sponsorship records do not guarantee sponsorship for a particular role.
      </footer>
    </main>
  );
}
