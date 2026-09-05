"use client";

import { useCallback, useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import Link from "next/link";

import { ApiError, apiRequest } from "@/lib/api";

type Company = {
  id: string;
  canonical_name: string;
  verification_status: string;
  is_saved: boolean;
  saved_company_id: string | null;
  saved_status: string | null;
  notify_current_jobs: boolean | null;
  baseline_completed: boolean | null;
  sources: Array<{
    id: string;
    provider: string;
    careers_url: string;
    status: string;
    last_success_at: string | null;
    next_poll_at: string | null;
    consecutive_failures: number;
    last_error_code: string | null;
  }>;
  sponsorship: {
    status: string;
    certified_cases: number;
    certified_workers: number;
    loaded_fiscal_years: number[];
    explanation: string;
  };
  warning: string | null;
};

type Resolution = {
  resolution: "existing" | "proposal" | "ambiguous";
  companies: Company[];
  message: string;
};

type CompanyList = { items: Company[]; next_cursor: string | null };

export function CompanyManager() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [query, setQuery] = useState("");
  const [careersUrl, setCareersUrl] = useState("");
  const [resolution, setResolution] = useState<Resolution | null>(null);
  const [notifyCurrentJobs, setNotifyCurrentJobs] = useState(false);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadCompanies = useCallback(async () => {
    try {
      const response = await apiRequest<CompanyList>("/companies?limit=100");
      setCompanies(response.items);
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not load saved companies.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timeout = window.setTimeout(() => void loadCompanies(), 0);
    return () => window.clearTimeout(timeout);
  }, [loadCompanies]);

  async function resolveCompany(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setWorking(true);
    setError(null);
    setResolution(null);
    try {
      const response = await apiRequest<Resolution>("/companies/resolve", {
        method: "POST",
        body: JSON.stringify({
          query,
          careers_url: careersUrl,
        }),
      });
      setResolution(response);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not resolve this company.",
      );
    } finally {
      setWorking(false);
    }
  }

  async function confirmSave(company: Company) {
    setWorking(true);
    setError(null);
    try {
      await apiRequest<Company>(`/companies/${company.id}/save`, {
        method: "POST",
        body: JSON.stringify({
          notify_current_jobs: notifyCurrentJobs,
        }),
      });
      setResolution(null);
      setQuery("");
      setCareersUrl("");
      setNotifyCurrentJobs(false);
      await loadCompanies();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not save this company.",
      );
    } finally {
      setWorking(false);
    }
  }

  async function updateCompany(company: Company, status: "active" | "paused") {
    if (!company.saved_company_id) return;
    setWorking(true);
    try {
      await apiRequest<Company>(
        `/saved-companies/${company.saved_company_id}`,
        {
          method: "PATCH",
          body: JSON.stringify({ status }),
        },
      );
      await loadCompanies();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not update this company.",
      );
    } finally {
      setWorking(false);
    }
  }

  async function updateFirstPollAlerts(company: Company) {
    if (!company.saved_company_id) return;
    setWorking(true);
    setError(null);
    try {
      await apiRequest<Company>(
        `/saved-companies/${company.saved_company_id}`,
        {
          method: "PATCH",
          body: JSON.stringify({
            notify_current_jobs: !company.notify_current_jobs,
          }),
        },
      );
      await loadCompanies();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not update first-poll alerts.",
      );
    } finally {
      setWorking(false);
    }
  }

  async function removeCompany(company: Company) {
    if (!company.saved_company_id) return;
    setWorking(true);
    try {
      await apiRequest<void>(`/saved-companies/${company.saved_company_id}`, {
        method: "DELETE",
      });
      await loadCompanies();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not remove this company.",
      );
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className="mt-10 grid gap-10">
      <form
        className="grid gap-5 rounded-2xl border border-white/10 bg-white/[0.04] p-6 sm:p-8"
        onSubmit={resolveCompany}
      >
        <div>
          <h2 className="text-xl font-semibold text-white">
            Add a company manually
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
            Enter the company name and careers page. JobScout will create a
            reviewable proposal and check whether that page can be monitored.
          </p>
        </div>
        <div className="grid gap-5 md:grid-cols-2">
          <label className="grid gap-2 text-sm text-slate-200">
            Company name or existing local name
            <input
              className="input"
              maxLength={200}
              onChange={(event) => setQuery(event.target.value)}
              required
              value={query}
            />
          </label>
          <label className="grid gap-2 text-sm text-slate-200">
            Careers page
            <input
              className="input"
              onChange={(event) => setCareersUrl(event.target.value)}
              placeholder="https://example.com/careers"
              required
              type="url"
              value={careersUrl}
            />
          </label>
        </div>
        <button
          className="button-primary justify-self-start"
          disabled={working}
          type="submit"
        >
          {working ? "Checking…" : "Review company"}
        </button>
      </form>

      {error ? (
        <p className="notice-error" role="alert">
          {error}
        </p>
      ) : null}
      {resolution ? (
        <section aria-live="polite" className="grid gap-5">
          <div>
            <h2 className="text-xl font-semibold text-white">
              Resolution result
            </h2>
            <p className="mt-2 text-sm text-slate-400">{resolution.message}</p>
          </div>
          {resolution.companies.map((company) => (
            <CompanyCard
              actions={
                company.is_saved ? null : (
                  <div className="grid gap-4 border-t border-white/10 pt-5">
                    <label className="flex max-w-2xl items-start gap-3 text-sm text-slate-300">
                      <input
                        checked={notifyCurrentJobs}
                        className="mt-1"
                        onChange={(event) =>
                          setNotifyCurrentJobs(event.target.checked)
                        }
                        type="checkbox"
                      />
                      <span>
                        Alert me about matching jobs already present on the
                        first poll. This may send an email soon after saving.
                      </span>
                    </label>
                    <button
                      className="button-primary justify-self-start"
                      disabled={working}
                      onClick={() => void confirmSave(company)}
                      type="button"
                    >
                      Confirm and save
                    </button>
                  </div>
                )
              }
              company={company}
              key={company.id}
            />
          ))}
        </section>
      ) : null}

      <section>
        <div className="mb-5">
          <h2 className="text-2xl font-semibold text-white">Saved companies</h2>
          <p className="mt-2 text-sm text-slate-400">
            Monitor source health, pause collection, or open a company&apos;s
            job history.
          </p>
        </div>
        {loading ? (
          <p className="text-slate-400">Loading saved companies…</p>
        ) : null}
        {!loading && companies.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-white/15 p-8 text-slate-400">
            No companies saved yet. Resolve one above, review its careers page,
            then confirm.
          </div>
        ) : null}
        <div className="grid gap-5">
          {companies.map((company) => (
            <CompanyCard
              actions={
                <div className="flex flex-wrap gap-3 border-t border-white/10 pt-5">
                  {company.baseline_completed ? (
                    <span className="rounded-lg border border-white/10 px-4 py-2 text-sm text-slate-400">
                      First-poll baseline complete
                    </span>
                  ) : (
                    <button
                      className="button-secondary"
                      disabled={working}
                      onClick={() => void updateFirstPollAlerts(company)}
                      type="button"
                    >
                      First-poll alerts:{" "}
                      {company.notify_current_jobs ? "On" : "Off"}
                    </button>
                  )}
                  <button
                    className="button-secondary"
                    disabled={working}
                    onClick={() =>
                      void updateCompany(
                        company,
                        company.saved_status === "paused" ? "active" : "paused",
                      )
                    }
                    type="button"
                  >
                    {company.saved_status === "paused" ? "Resume" : "Pause"}
                  </button>
                  <button
                    className="button-danger"
                    disabled={working}
                    onClick={() => void removeCompany(company)}
                    type="button"
                  >
                    Remove
                  </button>
                </div>
              }
              company={company}
              key={company.id}
            />
          ))}
        </div>
      </section>
    </div>
  );
}

function CompanyCard({
  actions,
  company,
}: {
  actions: ReactNode;
  company: Company;
}) {
  return (
    <article className="grid gap-5 rounded-2xl border border-white/10 bg-slate-950/45 p-6 sm:p-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <h3 className="text-xl font-semibold text-white">
          {company.canonical_name}
        </h3>
        <span className="rounded-full border border-white/15 px-3 py-1 text-xs uppercase tracking-wide text-slate-300">
          {company.verification_status}
        </span>
      </div>
      <dl className="text-sm">
        <div>
          <dt className="text-slate-500">H-1B sponsorship history</dt>
          <dd className="mt-1 text-slate-200">
            {company.sponsorship.status === "historical_records"
              ? `${company.sponsorship.certified_cases} certified cases across FY${company.sponsorship.loaded_fiscal_years.join(", FY")}`
              : company.sponsorship.status === "no_records"
                ? "No historical records found"
                : "No confident employer match yet"}
          </dd>
        </div>
      </dl>
      <p className="text-xs leading-5 text-slate-500">
        {company.sponsorship.explanation}
      </p>
      {company.warning ? (
        <p className="notice-warning">{company.warning}</p>
      ) : null}
      {company.is_saved ? (
        <div className="grid gap-3 border-t border-white/10 pt-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h4 className="text-sm font-semibold text-slate-200">
              Monitoring sources
            </h4>
            <Link
              className="text-sm text-cyan-300 hover:underline"
              href={`/companies/${company.id}`}
            >
              View source health and jobs
            </Link>
          </div>
          {company.sources.map((source) => (
            <div
              className="rounded-xl border border-white/10 bg-black/15 px-4 py-3 text-sm"
              key={source.id}
            >
              <div className="flex flex-wrap justify-between gap-2">
                <span className="font-medium text-slate-200">
                  {source.provider.replace("_", " ")}
                </span>
                <span className="uppercase tracking-wide text-slate-400">
                  {source.status.replace("_", " ")}
                </span>
              </div>
              <p className="mt-2 text-xs text-slate-500">
                {source.last_success_at
                  ? `Last successful poll ${new Date(source.last_success_at).toLocaleString()}`
                  : "Waiting for the first successful poll"}
                {source.consecutive_failures > 0
                  ? ` · ${source.consecutive_failures} consecutive failures`
                  : ""}
              </p>
              <a
                className="mt-2 inline-block text-xs text-cyan-300 hover:underline"
                href={source.careers_url}
                rel="noreferrer"
                target="_blank"
              >
                Open careers page
              </a>
            </div>
          ))}
        </div>
      ) : null}
      {actions}
    </article>
  );
}
