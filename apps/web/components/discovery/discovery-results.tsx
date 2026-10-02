"use client";

import { Check, ExternalLink, LoaderCircle } from "lucide-react";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ApiError, apiRequest } from "@/lib/api";

type Run = {
  id: string;
  query: string;
  status: "queued" | "running" | "succeeded" | "failed";
  cached: boolean;
  result_count: number;
  error_code: string | null;
  estimated_cost_usd: number;
};

type Result = {
  id: string;
  company_id: string;
  company_name: string;
  official_website_url: string | null;
  careers_url: string | null;
  research_source_status: "matched" | "unmatched";
  internship_research_reported: boolean;
  current_openings_count: number;
  explanation: string;
  historical_h1b_status: "historical_records" | "no_records" | "unresolved";
  certified_h1b_cases: number;
  loaded_fiscal_years: number[];
  careers_url_status:
    | "research_linked"
    | "page_checked"
    | "not_found"
    | "rejected";
  careers_url_reason: string;
  monitoring_support:
    | "structured"
    | "generic_verified"
    | "generic_pending"
    | "unsupported";
  is_hidden: boolean;
  is_saved: boolean;
};

type ResultPage = {
  items: Result[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
};

const RESULTS_PAGE_LIMIT = 20;

export function DiscoveryResults() {
  const params = useParams<{ runId: string }>();
  const runId = params.runId;
  const [run, setRun] = useState<Run | null>(null);
  const [results, setResults] = useState<Result[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [working, setWorking] = useState<string | null>(null);
  const [continuationRunId, setContinuationRunId] = useState<string | null>(
    null,
  );

  const load = useCallback(async () => {
    try {
      const nextRun = await apiRequest<Run>(`/discoveries/${runId}`);
      setRun(nextRun);
      if (nextRun.status === "succeeded") {
        const loadedResults: Result[] = [];
        let offset = 0;

        while (true) {
          const response = await apiRequest<ResultPage>(
            `/discoveries/${runId}/results?offset=${offset}&limit=${RESULTS_PAGE_LIMIT}`,
          );
          loadedResults.push(...response.items);

          if (!response.has_more) break;

          const nextOffset = response.offset + response.items.length;
          if (nextOffset <= offset) {
            throw new Error("Discovery result pagination did not advance.");
          }
          offset = nextOffset;
        }

        setResults(loadedResults);
      }
      return nextRun.status;
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "The discovery run could not be loaded.",
      );
      return "failed";
    }
  }, [runId]);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      const status = await load();
      if (active && (status === "queued" || status === "running")) {
        timer = setTimeout(() => void poll(), 2000);
      }
    }
    void poll();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [load]);

  useEffect(() => {
    if (!continuationRunId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function pollContinuation() {
      let finished = false;
      try {
        const continuation = await apiRequest<Run>(
          `/discoveries/${continuationRunId}`,
        );
        if (!active) return;
        if (
          continuation.status === "queued" ||
          continuation.status === "running"
        ) {
          timer = setTimeout(() => void pollContinuation(), 2000);
          return;
        }
        if (continuation.status === "failed") {
          finished = true;
          setError(
            `Additional research stopped with error ${continuation.error_code ?? "DISCOVERY_FAILED"}.`,
          );
        } else {
          finished = true;
          await load();
          setNotice(
            continuation.result_count > 0
              ? `Found ${continuation.result_count} additional ${continuation.result_count === 1 ? "company" : "companies"}.`
              : "No additional companies were found. Your previous results are unchanged.",
          );
        }
      } catch (caught) {
        finished = true;
        if (active) {
          setError(
            caught instanceof ApiError
              ? caught.message
              : "The additional research run could not be loaded.",
          );
        }
      } finally {
        if (active && finished) {
          setWorking(null);
          setContinuationRunId(null);
        }
      }
    }

    void pollContinuation();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [continuationRunId, load]);

  async function save(result: Result) {
    setWorking(`save:${result.id}`);
    setError(null);
    try {
      await apiRequest(`/discoveries/${runId}/save-selected`, {
        method: "POST",
        body: JSON.stringify({ result_ids: [result.id] }),
      });
      await load();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "The company could not be saved.",
      );
    } finally {
      setWorking(null);
    }
  }

  async function researchMore() {
    setWorking("research-more");
    setError(null);
    setNotice(
      "Additional research is queued. Existing results remain available while it runs.",
    );
    try {
      const continuation = await apiRequest<Run>(
        `/discoveries/${runId}/research-more`,
        {
          method: "POST",
          body: JSON.stringify({ acknowledge_additional_api_usage: true }),
        },
      );
      setContinuationRunId(continuation.id);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Additional company research could not be started.",
      );
      setWorking(null);
    }
  }

  async function saveAll() {
    setWorking("all");
    setError(null);
    try {
      await apiRequest(`/discoveries/${runId}/save-all-monitorable`, {
        method: "POST",
      });
      await load();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "The supported companies could not be saved.",
      );
    } finally {
      setWorking(null);
    }
  }

  async function hide(result: Result) {
    setWorking(`hide:${result.id}`);
    setError(null);
    setNotice(null);
    try {
      await apiRequest(`/discoveries/${runId}/results/${result.id}/hide`, {
        method: "POST",
      });
      setResults((current) =>
        current.map((item) =>
          item.id === result.id ? { ...item, is_hidden: true } : item,
        ),
      );
      setNotice(
        `${result.company_name} is hidden. Use Show result to restore it.`,
      );
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "The result could not be hidden.",
      );
    } finally {
      setWorking(null);
    }
  }

  async function show(result: Result) {
    setWorking(`show:${result.id}`);
    setError(null);
    setNotice(null);
    try {
      await apiRequest(`/discoveries/${runId}/results/${result.id}/show`, {
        method: "POST",
      });
      setResults((current) =>
        current.map((item) =>
          item.id === result.id ? { ...item, is_hidden: false } : item,
        ),
      );
      setNotice(`${result.company_name} is visible again.`);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "The result could not be shown.",
      );
    } finally {
      setWorking(null);
    }
  }

  if (!run) {
    return (
      <p className="mt-10 flex items-center gap-3 text-slate-300">
        <LoaderCircle className="animate-spin" size={20} /> Loading research…
      </p>
    );
  }

  const visible = results.filter((result) => !result.is_hidden);

  return (
    <div className="mt-10 grid gap-7">
      <RunSummary run={run} />
      {error ? (
        <p className="notice-error" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p className="notice-success" role="status">
          {notice}
        </p>
      ) : null}
      {run.status === "queued" || run.status === "running" ? (
        <div className="rounded-2xl border border-cyan-300/20 bg-cyan-300/[0.06] p-8">
          <p className="flex items-center gap-3 font-medium text-cyan-100">
            <LoaderCircle className="animate-spin" size={20} />
            {run.status === "queued"
              ? "Waiting for the research worker…"
              : "Researching company suggestions and careers sources…"}
          </p>
          <p className="mt-3 text-sm leading-6 text-slate-400">
            You can leave this page and return later. The worker records
            progress in the local database.
          </p>
        </div>
      ) : null}
      {run.status === "failed" ? (
        <p className="notice-error" role="alert">
          Research stopped with error {run.error_code ?? "DISCOVERY_FAILED"}.
          Check the API and worker logs, then try again.
        </p>
      ) : null}
      {run.status === "succeeded" ? (
        <>
          <div className="flex flex-wrap items-center justify-between gap-4">
            <p className="text-sm leading-6 text-slate-400">
              Loaded {results.length} company suggestion
              {results.length === 1 ? "" : "s"} alphabetically: {visible.length}{" "}
              visible
              {results.length - visible.length > 0
                ? ` and ${results.length - visible.length} hidden`
                : ""}
              . Save and confirm marks a company verified for this local
              installation.
            </p>
            <button
              className="button-secondary"
              disabled={working !== null}
              onClick={() => void saveAll()}
              type="button"
            >
              {working === "all"
                ? "Saving…"
                : "Save and confirm companies with monitorable careers sources"}
            </button>
          </div>
          {visible.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-white/15 p-8 text-slate-400">
              No visible results remain in this run.
            </div>
          ) : null}
          <div className="grid gap-6">
            {results.map((result) =>
              result.is_hidden ? (
                <HiddenResultCard
                  busy={working !== null}
                  key={result.id}
                  onShow={() => void show(result)}
                  result={result}
                  showing={working === `show:${result.id}`}
                />
              ) : (
                <ResultCard
                  busy={working !== null}
                  hiding={working === `hide:${result.id}`}
                  key={result.id}
                  onHide={() => void hide(result)}
                  onSave={() => void save(result)}
                  result={result}
                  saving={working === `save:${result.id}`}
                />
              ),
            )}
          </div>
          <div className="grid justify-items-center gap-2">
            <button
              className="button-secondary"
              disabled={working !== null}
              onClick={() => void researchMore()}
              type="button"
            >
              {working === "research-more"
                ? "Researching more…"
                : "Research more companies"}
            </button>
            <p className="max-w-xl text-center text-xs leading-5 text-amber-200/80">
              Research more makes new OpenAI requests with your configured API
              key, so additional token and web-search charges may apply.
            </p>
          </div>
        </>
      ) : null}
    </div>
  );
}

function RunSummary({ run }: { run: Run }) {
  const cost = run.cached
    ? "Cache hit · $0 additional API cost · no new token use"
    : run.status === "succeeded" || run.status === "failed"
      ? `Estimated OpenAI cost: $${run.estimated_cost_usd.toFixed(4)}`
      : "The estimated OpenAI cost will appear when research finishes.";
  return (
    <section className="rounded-2xl border border-white/10 bg-white/[0.04] p-6 sm:p-8">
      <p className="text-sm uppercase tracking-[0.15em] text-cyan-300">
        {run.status}
      </p>
      <h2 className="mt-3 text-2xl font-semibold text-white">
        Research: {run.query}
      </h2>
      <p className="mt-3 text-sm text-slate-400">{cost}</p>
    </section>
  );
}

function ResultCard({
  result,
  busy,
  saving,
  hiding,
  onSave,
  onHide,
}: {
  result: Result;
  busy: boolean;
  saving: boolean;
  hiding: boolean;
  onSave: () => void;
  onHide: () => void;
}) {
  return (
    <article className="grid gap-6 rounded-2xl border border-white/10 bg-slate-950/45 p-6 sm:p-8">
      <div className="flex flex-wrap items-start justify-between gap-5">
        <div>
          <p className="text-sm font-medium text-cyan-300">
            {result.research_source_status === "matched"
              ? "Research source matched"
              : "AI suggestion — source not matched"}
          </p>
          <h2 className="mt-2 text-2xl font-semibold text-white">
            {result.company_name}
          </h2>
          <div className="mt-3 flex flex-wrap gap-4 text-sm">
            {result.official_website_url ? (
              <ExternalLinkText
                href={result.official_website_url}
                label="AI-provided website"
              />
            ) : null}
            {result.careers_url ? (
              <ExternalLinkText
                href={result.careers_url}
                label="Careers page"
              />
            ) : null}
          </div>
        </div>
        <div className="rounded-xl border border-cyan-300/20 bg-cyan-300/10 px-5 py-4 text-center">
          <p className="text-3xl font-semibold text-cyan-100">
            {result.current_openings_count}
          </p>
          <p className="mt-1 text-xs uppercase tracking-wide text-cyan-200/70">
            Openings found at discovery
          </p>
        </div>
      </div>

      <p className="leading-7 text-slate-300">{result.explanation}</p>

      <div className="grid gap-4 md:grid-cols-2">
        <EvidencePanel title="DOL filing facts">
          {h1bText(result)}
        </EvidencePanel>
        <EvidencePanel title="Internships">
          {result.internship_research_reported
            ? "AI research reported an internship program."
            : "AI research did not report an internship program."}
        </EvidencePanel>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <EvidencePanel title="Careers link verification">
          {careersUrlText(result)}
        </EvidencePanel>
        <EvidencePanel title="Automatic monitoring">
          {monitoringSupportText(result.monitoring_support)}
        </EvidencePanel>
      </div>

      <div className="flex flex-wrap gap-3 border-t border-white/10 pt-5">
        <button
          className="button-primary"
          disabled={busy || result.is_saved}
          onClick={onSave}
          type="button"
        >
          {result.is_saved ? <Check aria-hidden="true" size={18} /> : null}
          {result.is_saved
            ? "Saved and confirmed"
            : saving
              ? "Saving…"
              : "Save and confirm"}
        </button>
        <button
          className="button-secondary"
          disabled={busy}
          onClick={onHide}
          type="button"
        >
          {hiding ? "Hiding…" : "Hide result"}
        </button>
      </div>
    </article>
  );
}

function HiddenResultCard({
  result,
  busy,
  showing,
  onShow,
}: {
  result: Result;
  busy: boolean;
  showing: boolean;
  onShow: () => void;
}) {
  return (
    <article className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-dashed border-white/15 bg-slate-950/25 px-6 py-5">
      <div>
        <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
          Hidden result
        </p>
        <h2 className="mt-1 text-lg font-semibold text-slate-300">
          {result.company_name}
        </h2>
      </div>
      <button
        className="button-secondary"
        disabled={busy}
        onClick={onShow}
        type="button"
      >
        {showing ? "Showing…" : "Show result"}
      </button>
    </article>
  );
}

function EvidencePanel({
  title,
  children,
}: {
  title: string;
  children: string;
}) {
  return (
    <div className="rounded-xl bg-white/[0.04] p-4 text-sm leading-6 text-slate-400">
      <p className="font-medium text-slate-200">{title}</p>
      <p className="mt-2">{children}</p>
    </div>
  );
}

function ExternalLinkText({ href, label }: { href: string; label: string }) {
  return (
    <a
      className="inline-flex items-center gap-1 text-cyan-300 hover:text-cyan-200"
      href={href}
      rel="noreferrer"
      target="_blank"
    >
      {label} <ExternalLink aria-hidden="true" size={14} />
    </a>
  );
}

function h1bText(result: Result) {
  if (result.historical_h1b_status === "unresolved") {
    return "No exact normalized employer-name match was found in the loaded DOL data.";
  }
  if (result.historical_h1b_status === "no_records") {
    return "The exact normalized employer-name match has no records in the loaded DOL data.";
  }
  return `${result.certified_h1b_cases} certified H1-B filings found across ${result.loaded_fiscal_years.join(", ")} by exact normalized employer-name match. This does not prove the AI company identity or guarantee sponsorship.`;
}

function careersUrlText(result: Result) {
  if (result.careers_url_status === "page_checked") {
    if (result.careers_url_reason === "CAREERS_PAGE_NO_LISTING_FOUND") {
      return "JobScout opened and inspected this page but found no individual openings, so it will not be monitored.";
    }
    return "JobScout opened and inspected this careers page during discovery.";
  }
  if (result.careers_url_status === "research_linked") {
    return "AI research returned this careers link, but JobScout did not successfully inspect the page.";
  }
  if (result.careers_url_status === "rejected") {
    return `A proposed careers source was rejected because it did not match its evidence (${result.careers_url_reason}).`;
  }
  return "AI research did not provide an accepted careers source.";
}

function monitoringSupportText(status: Result["monitoring_support"]) {
  if (status === "structured") {
    return "After you save this company, JobScout can check this careers page automatically while your local services are running.";
  }
  if (status === "generic_verified") {
    return "JobScout confirmed this page lists individual openings and can collect them automatically after you save the company. Pages like this are add/update-only, so a job disappearing is never treated as closed.";
  }
  if (status === "generic_pending") {
    return "The careers link came from research, but JobScout has not confirmed that automatic collection will work.";
  }
  return "JobScout does not currently have a careers source it can monitor automatically.";
}
