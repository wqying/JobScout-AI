"use client";

import { Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

import { ApiError, apiRequest } from "@/lib/api";
import { DailyDiscoveryHistory } from "@/components/discovery/daily-history";

type DiscoveryRun = {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  cached: boolean;
};

export function DiscoverySearch() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setWorking(true);
    setError(null);
    try {
      const run = await apiRequest<DiscoveryRun>("/discoveries", {
        method: "POST",
        body: JSON.stringify({ query, country: "US", limit: 20 }),
      });
      router.push(`/discover/${run.id}`);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "The research request could not be started.",
      );
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className="mt-10 grid gap-3">
      <form
        className="grid gap-5 rounded-2xl border border-white/10 bg-white/[0.04] p-6 sm:p-8"
        onSubmit={submit}
      >
        <label className="grid gap-2 text-sm text-slate-200">
          Industry or company category
          <div className="flex flex-col gap-3 sm:flex-row">
            <input
              autoComplete="off"
              className="input flex-1"
              maxLength={120}
              minLength={3}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="gaming companies"
              required
              value={query}
            />
            <button className="button-primary" disabled={working} type="submit">
              <Search aria-hidden="true" size={18} />
              {working ? "Starting research…" : "Research companies"}
            </button>
          </div>
        </label>
        <div className="grid gap-2 text-sm leading-6 text-slate-400 sm:grid-cols-2">
          <p>
            New searches are limited to five per day by default. This prevents
            an accidental loop from repeatedly charging your OpenAI account.
          </p>
          <p>
            Matching successful searches are reused for seven days. A cache hit
            makes no OpenAI request and uses no additional tokens.
          </p>
        </div>
        {error ? (
          <p className="notice-error" role="alert">
            {error}
          </p>
        ) : null}
      </form>
      <DailyDiscoveryHistory />
    </div>
  );
}
