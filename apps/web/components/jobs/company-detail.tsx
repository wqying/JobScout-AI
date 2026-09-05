"use client";

import { useCallback, useEffect, useState } from "react";

import { EmailAlertImportPanel } from "@/components/jobs/email-alert-import-panel";
import { JobList } from "@/components/jobs/job-list";
import {
  monitoringMode,
  sourceDiagnostic,
  SourceRecoveryPanel,
} from "@/components/jobs/source-recovery-panel";
import { ApiError, apiRequest } from "@/lib/api";

type CompanyDetail = {
  id: string;
  canonical_name: string;
  saved_company_id: string | null;
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
};

export function CompanyDetailView({ companyId }: { companyId: string }) {
  const [company, setCompany] = useState<CompanyDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [pollingSourceId, setPollingSourceId] = useState<string | null>(null);
  const [pollMessage, setPollMessage] = useState<string | null>(null);

  const loadCompany = useCallback(async () => {
    try {
      setCompany(await apiRequest<CompanyDetail>(`/companies/${companyId}`));
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not load this company.",
      );
    }
  }, [companyId]);

  useEffect(() => {
    let active = true;
    const timeout = window.setTimeout(() => {
      if (active) void loadCompany();
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timeout);
    };
  }, [loadCompany]);

  async function pollNow(sourceId: string) {
    setPollingSourceId(sourceId);
    setPollMessage(null);
    setPollError(null);
    try {
      const response = await apiRequest<{ message: string }>(
        `/ops/sources/${sourceId}/poll`,
        { method: "POST" },
      );
      setPollMessage(
        `${response.message} New matching jobs will create alerts after the worker finishes.`,
      );
    } catch (caught) {
      setPollError(
        caught instanceof ApiError
          ? caught.message
          : "Could not queue this source poll.",
      );
    } finally {
      setPollingSourceId(null);
    }
  }

  if (error)
    return (
      <p className="notice-error mt-10" role="alert">
        {error}
      </p>
    );
  if (!company) return <p className="mt-10 text-slate-400">Loading company…</p>;

  return (
    <div className="mt-10 grid gap-10">
      <section>
        <h1 className="text-4xl font-semibold text-white">
          {company.canonical_name}
        </h1>
      </section>
      <section>
        <h2 className="text-2xl font-semibold text-white">Source health</h2>
        {pollMessage ? (
          <p className="notice-success mt-4" role="status">
            {pollMessage}
          </p>
        ) : null}
        {pollError ? (
          <p className="notice-error mt-4" role="alert">
            {pollError}
          </p>
        ) : null}
        <div className="mt-5 grid gap-4">
          {company.sources.map((source) => (
            <article
              className="rounded-2xl border border-white/10 bg-white/[0.04] p-5"
              key={source.id}
            >
              <div className="flex flex-wrap justify-between gap-3">
                <h3 className="font-semibold capitalize text-white">
                  {source.provider.replaceAll("_", " ")}
                </h3>
                <div className="flex flex-wrap gap-2 text-xs uppercase tracking-wide">
                  <span className="rounded-full border border-cyan-300/20 px-3 py-1 text-cyan-200">
                    {monitoringMode(source)}
                  </span>
                  <span className="rounded-full border border-white/15 px-3 py-1 text-slate-400">
                    {source.status.replaceAll("_", " ")}
                  </span>
                </div>
              </div>
              {source.provider === "email_alert" ? (
                <p className="mt-2 text-sm text-slate-400">
                  Local employer-email observations
                </p>
              ) : (
                <a
                  className="mt-2 block break-all text-sm text-cyan-300 hover:underline"
                  href={source.careers_url}
                  rel="noreferrer"
                  target="_blank"
                >
                  {source.careers_url}
                </a>
              )}
              <dl className="mt-4 grid gap-2 text-sm text-slate-400 sm:grid-cols-2">
                <div>
                  <dt className="text-slate-500">Last success</dt>
                  <dd>
                    {source.last_success_at
                      ? new Date(source.last_success_at).toLocaleString()
                      : "Not yet"}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500">Next eligible poll</dt>
                  <dd>
                    {source.next_poll_at
                      ? new Date(source.next_poll_at).toLocaleString()
                      : "Not scheduled"}
                  </dd>
                </div>
              </dl>
              {source.last_error_code ? (
                <p className="notice-warning mt-4">
                  {source.consecutive_failures} consecutive failures ·{" "}
                  {source.last_error_code}
                  <span className="mt-1 block text-xs">
                    {sourceDiagnostic(source.last_error_code)}
                  </span>
                </p>
              ) : null}
              <button
                className="button-secondary mt-4"
                disabled={
                  pollingSourceId !== null ||
                  !["pending_resolution", "supported", "degraded"].includes(
                    source.status,
                  )
                }
                onClick={() => void pollNow(source.id)}
                type="button"
              >
                {pollingSourceId === source.id ? "Queuing poll…" : "Poll now"}
              </button>
              <SourceRecoveryPanel
                companyId={companyId}
                onSourceChanged={loadCompany}
                savedCompanyId={company.saved_company_id}
                source={source}
              />
            </article>
          ))}
        </div>
      </section>
      <EmailAlertImportPanel companyId={companyId} />
      <section>
        <h2 className="mb-5 text-2xl font-semibold text-white">Jobs</h2>
        <JobList companyId={companyId} />
      </section>
    </div>
  );
}
