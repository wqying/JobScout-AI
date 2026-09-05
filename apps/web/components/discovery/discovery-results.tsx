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

type Source = {
  source_id: string | null;
  url: string;
  title: string | null;
  source_type: string;
  supports_claims: string[];
};

type Result = {
  id: string;
  company_id: string;
  rank: number;
  company_name: string;
  careers_url: string | null;
  opportunity_score: number;
  scores: {
    industry: number;
    historical_h1b_sponsorship: number;
    internship: number;
    careers_page_support: number;
    current_openings: number;
  };
  explanation: string;
  historical_h1b_status: "historical_records" | "no_records" | "unresolved";
  certified_h1b_cases: number;
  loaded_fiscal_years: number[];
  internship_evidence: boolean;
  careers_url_status: "evidence_verified" | "not_found" | "rejected";
  careers_url_reason: string;
  monitoring_support:
    | "structured"
    | "generic_verified"
    | "generic_pending"
    | "unsupported";
  sources: Source[];
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
              ? `Found ${continuation.result_count} additional verified ${continuation.result_count === 1 ? "company" : "companies"}.`
              : "No additional verified companies were found. Your previous results are unchanged.",
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
              : "Researching and verifying company sources…"}
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
              Loaded {results.length} verified recommendation
              {results.length === 1 ? "" : "s"}: {visible.length} visible
              {results.length - visible.length > 0
                ? ` and ${results.length - visible.length} hidden`
                : ""}
              . Scores always include their component breakdown.
            </p>
            <button
              className="button-secondary"
              disabled={working !== null}
              onClick={() => void saveAll()}
              type="button"
            >
              {working === "all" ? "Saving…" : "Save all with careers sources"}
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
            Rank #{result.rank}
          </p>
          <h2 className="mt-2 text-2xl font-semibold text-white">
            {result.company_name}
          </h2>
          <div className="mt-3 flex flex-wrap gap-4 text-sm">
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
            {result.opportunity_score}
          </p>
          <p className="mt-1 text-xs uppercase tracking-wide text-cyan-200/70">
            Opportunity score
          </p>
        </div>
      </div>

      <p className="leading-7 text-slate-300">{result.explanation}</p>

      <ScoreGrid result={result} />

      <div className="grid gap-4 md:grid-cols-2">
        <EvidencePanel title="H1-B sponsorship">
          {h1bText(result)}
        </EvidencePanel>
        <EvidencePanel title="Internships">
          {result.internship_evidence
            ? "Official internship evidence found."
            : "No verified official internship evidence found."}
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

      <details className="rounded-xl border border-white/10 p-4">
        <summary className="cursor-pointer font-medium text-slate-200">
          Evidence sources ({result.sources.length})
        </summary>
        <ul className="mt-4 grid gap-3 text-sm text-slate-400">
          {result.sources.map((source) => (
            <li key={source.url}>
              <ExternalLinkText
                href={source.url}
                label={source.title ?? source.url}
              />
              <span className="ml-2 text-slate-500">
                Supports: {source.supports_claims.join(", ")}
              </span>
            </li>
          ))}
        </ul>
      </details>

      <div className="flex flex-wrap gap-3 border-t border-white/10 pt-5">
        <button
          className="button-primary"
          disabled={busy || result.is_saved}
          onClick={onSave}
          type="button"
        >
          {result.is_saved ? <Check aria-hidden="true" size={18} /> : null}
          {result.is_saved ? "Saved" : saving ? "Saving…" : "Save company"}
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
          Hidden result · Rank #{result.rank}
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

function ScoreGrid({ result }: { result: Result }) {
  const scores = [
    ["Industry", result.scores.industry],
    ["Historical H1-B", result.scores.historical_h1b_sponsorship],
    ["Internship", result.scores.internship],
    ["Careers page", result.scores.careers_page_support],
    ["Current openings", result.scores.current_openings],
  ];
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
      {scores.map(([label, value]) => (
        <div className="rounded-xl bg-white/[0.04] p-3" key={label}>
          <p className="text-xl font-semibold text-white">{value}</p>
          <p className="mt-1 text-xs text-slate-500">{label}</p>
        </div>
      ))}
    </div>
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
    return "No confident match was found in the loaded H1-B sponsorship data.";
  }
  if (result.historical_h1b_status === "no_records") {
    return "No matching records were found in the loaded H1-B sponsorship data.";
  }
  return `${result.certified_h1b_cases} certified H1-B filings found across ${result.loaded_fiscal_years.join(", ")}. Historical activity does not guarantee sponsorship for this role.`;
}

function careersUrlText(result: Result) {
  if (result.careers_url_status === "evidence_verified") {
    if (result.careers_url_reason.startsWith("CAREERS_PAGE_LISTING_VERIFIED")) {
      return "JobScout opened this page during research and confirmed it lists individual openings.";
    }
    if (result.careers_url_reason === "CAREERS_PAGE_ATS_DISCOVERED") {
      return "JobScout opened the cited careers page during research and followed it to the company's job board.";
    }
    if (result.careers_url_reason === "CAREERS_PAGE_NO_LISTING_FOUND") {
      return "JobScout opened this page during research and found no individual job openings on it, so it will not be monitored.";
    }
    return "This link comes directly from a source returned by the research search. JobScout has not opened it.";
  }
  if (result.careers_url_status === "rejected") {
    return `A proposed careers source was rejected because it did not match its evidence (${result.careers_url_reason}).`;
  }
  return "No manifest-backed careers source was selected by the research workflow.";
}

function monitoringSupportText(status: Result["monitoring_support"]) {
  if (status === "structured") {
    return "After you save this company, JobScout can check this careers page automatically while your local services are running.";
  }
  if (status === "generic_verified") {
    return "JobScout confirmed this page lists individual openings and can collect them automatically after you save the company. Pages like this are add/update-only, so a job disappearing is never treated as closed.";
  }
  if (status === "generic_pending") {
    return "The careers link is verified, but JobScout must inspect the page after saving before it can promise automatic collection.";
  }
  return "JobScout does not currently have a verified source it can monitor automatically.";
}
