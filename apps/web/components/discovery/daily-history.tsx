"use client";

import { LoaderCircle, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiRequest } from "@/lib/api";
import {
  getLocalDayWindow,
  millisecondsUntilNextLocalDay,
} from "@/lib/local-day";

type DiscoveryStatus = "queued" | "running" | "succeeded" | "failed";

type DailyHistoryRun = {
  id: string;
  query: string;
  status: DiscoveryStatus;
  cached: boolean;
  result_count: number;
  error_code: string | null;
  created_at: string;
};

type DailyHistoryResponse = {
  items: DailyHistoryRun[];
  next_cursor: string | null;
};

const ACTIVE_REFRESH_MS = 3_000;
const CLOCK_CHECK_MAX_MS = 60_000;

export function DailyDiscoveryHistory() {
  const [items, setItems] = useState<DailyHistoryRun[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);
  const localDayStart = useRef(getLocalDayWindow().start);

  const load = useCallback(async (resetDay = false) => {
    const requestedDay = getLocalDayWindow().start;
    if (resetDay) {
      setItems([]);
      setNextCursor(null);
      setLoading(true);
    }

    try {
      const response = await apiRequest<DailyHistoryResponse>(
        "/discoveries/daily-history?limit=10",
      );
      if (!mounted.current || getLocalDayWindow().start !== requestedDay) {
        return;
      }
      localDayStart.current = requestedDay;
      setItems(response.items);
      setNextCursor(response.next_cursor);
      setError(null);
    } catch (caught) {
      if (!mounted.current) return;
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Today's search history could not be loaded.",
      );
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  const loadEarlier = useCallback(async () => {
    if (!nextCursor || loadingEarlier) return;
    const requestedDay = getLocalDayWindow().start;
    setLoadingEarlier(true);
    try {
      const response = await apiRequest<DailyHistoryResponse>(
        `/discoveries/daily-history?limit=10&cursor=${encodeURIComponent(nextCursor)}`,
      );
      if (!mounted.current || getLocalDayWindow().start !== requestedDay) {
        return;
      }
      setItems((current) => {
        const currentIds = new Set(current.map((item) => item.id));
        return [
          ...current,
          ...response.items.filter((item) => !currentIds.has(item.id)),
        ];
      });
      setNextCursor(response.next_cursor);
      setError(null);
    } catch (caught) {
      if (!mounted.current) return;
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Earlier searches could not be loaded.",
      );
    } finally {
      if (mounted.current) setLoadingEarlier(false);
    }
  }, [loadingEarlier, nextCursor]);

  const refreshForCurrentDay = useCallback(() => {
    const currentDay = getLocalDayWindow().start;
    const changedDay = currentDay !== localDayStart.current;
    if (changedDay) localDayStart.current = currentDay;
    void load(changedDay);
  }, [load]);

  useEffect(() => {
    mounted.current = true;
    const timer = window.setTimeout(() => void load(), 0);
    return () => {
      window.clearTimeout(timer);
      mounted.current = false;
    };
  }, [load]);

  const hasActiveRun = items.some(
    (item) => item.status === "queued" || item.status === "running",
  );

  useEffect(() => {
    if (!hasActiveRun) return;
    const timer = window.setTimeout(() => void load(), ACTIVE_REFRESH_MS);
    return () => window.clearTimeout(timer);
  }, [hasActiveRun, items, load]);

  useEffect(() => {
    function onFocus() {
      refreshForCurrentDay();
    }

    function onVisibilityChange() {
      if (document.visibilityState === "visible") refreshForCurrentDay();
    }

    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [refreshForCurrentDay]);

  useEffect(() => {
    let timer: number | undefined;

    function checkClockAndSchedule() {
      const currentDay = getLocalDayWindow().start;
      if (currentDay !== localDayStart.current) {
        localDayStart.current = currentDay;
        void load(true);
      }
      const delay = Math.max(
        50,
        Math.min(
          millisecondsUntilNextLocalDay() + 50,
          CLOCK_CHECK_MAX_MS,
        ),
      );
      timer = window.setTimeout(checkClockAndSchedule, delay);
    }

    checkClockAndSchedule();
    return () => {
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [load]);

  return (
    <section
      aria-busy={loading}
      aria-labelledby="daily-history-heading"
      className="grid gap-4 rounded-2xl border border-white/10 bg-slate-950/35 px-5 py-4 sm:px-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2
            className="text-sm font-semibold uppercase tracking-[0.14em] text-cyan-200"
            id="daily-history-heading"
          >
            Daily history
          </h2>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            Today&apos;s industry searches, refreshed at midnight on this
            computer.
          </p>
        </div>
        {!loading ? (
          <button
            className="inline-flex items-center gap-2 text-xs font-medium text-slate-300 underline decoration-white/20 underline-offset-4 hover:text-white"
            onClick={() => void load()}
            type="button"
          >
            <RotateCcw aria-hidden="true" size={13} />
            Refresh
          </button>
        ) : null}
      </div>

      {loading && items.length === 0 ? (
        <p className="flex items-center gap-2 text-sm text-slate-400" role="status">
          <LoaderCircle aria-hidden="true" className="animate-spin" size={16} />
          Loading today&apos;s searches…
        </p>
      ) : null}

      {error ? (
        <div className="notice-error flex flex-wrap items-center justify-between gap-3" role="alert">
          <span>{error}</span>
          <button
            className="font-semibold underline underline-offset-4"
            onClick={() => void load(items.length === 0)}
            type="button"
          >
            Retry
          </button>
        </div>
      ) : null}

      {!loading && !error && items.length === 0 ? (
        <p className="text-sm text-slate-400">
          No industries searched today yet. Your next search will appear here.
        </p>
      ) : null}

      {items.length > 0 ? (
        <div className="grid gap-3">
          <ul className="grid gap-2">
            {items.map((item) => (
              <li key={item.id}>
                <Link
                  aria-label={`Open ${item.query} search (${statusLabel(item.status)})`}
                  className="flex flex-col gap-2 rounded-xl border border-white/10 bg-white/[0.035] px-4 py-3 transition hover:border-cyan-300/30 hover:bg-cyan-300/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300 sm:flex-row sm:items-center sm:justify-between"
                  href={`/discover/${item.id}`}
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium text-white">
                      {item.query}
                    </span>
                    <span className="mt-1 block text-xs text-slate-500">
                      Searched{" "}
                      <time dateTime={item.created_at}>
                        {formatLocalTime(item.created_at)}
                      </time>
                    </span>
                  </span>
                  <span className="flex shrink-0 flex-wrap items-center gap-2 text-xs">
                    <span className={statusClass(item.status)}>
                      {statusLabel(item.status)}
                    </span>
                    {item.cached ? (
                      <span className="rounded-full bg-violet-300/10 px-2 py-1 text-violet-200">
                        Cached
                      </span>
                    ) : null}
                    {item.status === "succeeded" ? (
                      <span className="text-slate-400">
                        {item.result_count} result
                        {item.result_count === 1 ? "" : "s"}
                      </span>
                    ) : null}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          {nextCursor ? (
            <button
              className="justify-self-start text-xs font-medium text-cyan-200 underline decoration-cyan-200/30 underline-offset-4 hover:text-cyan-100 disabled:cursor-wait disabled:text-slate-500"
              disabled={loadingEarlier}
              onClick={() => void loadEarlier()}
              type="button"
            >
              {loadingEarlier ? "Loading earlier searches…" : "Load earlier today"}
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function statusLabel(status: DiscoveryStatus): string {
  return {
    queued: "Queued",
    running: "Researching",
    succeeded: "Ready",
    failed: "Failed",
  }[status];
}

function statusClass(status: DiscoveryStatus): string {
  const color = {
    queued: "bg-amber-300/10 text-amber-200",
    running: "bg-cyan-300/10 text-cyan-200",
    succeeded: "bg-emerald-300/10 text-emerald-200",
    failed: "bg-rose-300/10 text-rose-200",
  }[status];
  return `rounded-full px-2 py-1 font-medium ${color}`;
}

function formatLocalTime(value: string): string {
  return new Date(value).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
}
