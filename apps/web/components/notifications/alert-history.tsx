"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { ApiError, apiRequest } from "@/lib/api";

type AlertJob = {
  job_id: string;
  title: string;
  location_text: string | null;
  role_type: string;
  apply_url: string;
  event_type: "discovered" | "reopened";
  first_seen_at: string;
  match_reasons: string[];
};

export type AlertItem = {
  id: string;
  outbox_id: string;
  title: string;
  body: string;
  action_url: string;
  read_at: string | null;
  created_at: string;
  company_id: string | null;
  company_name: string | null;
  jobs: AlertJob[];
};

type AlertListResponse = {
  items: AlertItem[];
  next_cursor: string | null;
  unread_count: number;
};

export function AlertHistory() {
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [markingReadIds, setMarkingReadIds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const requestGeneration = useRef(0);

  useEffect(() => {
    let active = true;
    const generation = requestGeneration.current;
    const query = notificationQuery(unreadOnly);
    void apiRequest<AlertListResponse>(query)
      .then((response) => {
        if (!active || generation !== requestGeneration.current) return;
        setAlerts(response.items);
        setUnreadCount(response.unread_count);
        setNextCursor(response.next_cursor);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (active) {
          setError(
            caught instanceof ApiError
              ? caught.message
              : "Could not load your alert history.",
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [unreadOnly]);

  async function markRead(id: string) {
    setMarkingReadIds((current) => [...current, id]);
    setError(null);
    try {
      const updated = await apiRequest<AlertItem>(`/notifications/${id}/read`, {
        method: "POST",
      });
      setUnreadCount((current) => Math.max(0, current - 1));
      setAlerts((current) =>
        unreadOnly
          ? current.filter((alert) => alert.id !== id)
          : current.map((alert) => (alert.id === id ? updated : alert)),
      );
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not mark that alert as read.",
      );
    } finally {
      setMarkingReadIds((current) => current.filter((item) => item !== id));
    }
  }

  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    const generation = requestGeneration.current;
    setLoadingMore(true);
    setError(null);
    try {
      const response = await apiRequest<AlertListResponse>(
        notificationQuery(unreadOnly, nextCursor),
      );
      if (generation !== requestGeneration.current) return;
      setAlerts((current) => mergeUniqueAlerts(current, response.items));
      setUnreadCount(response.unread_count);
      setNextCursor(response.next_cursor);
    } catch (caught) {
      if (generation === requestGeneration.current) {
        setError(
          caught instanceof ApiError
            ? caught.message
            : "Could not load older alerts.",
        );
      }
    } finally {
      if (generation === requestGeneration.current) setLoadingMore(false);
    }
  }

  function changeUnreadFilter(checked: boolean) {
    requestGeneration.current += 1;
    setLoading(true);
    setLoadingMore(false);
    setAlerts([]);
    setNextCursor(null);
    setError(null);
    setUnreadOnly(checked);
  }

  return (
    <section
      aria-busy={loading || loadingMore}
      aria-labelledby="in-app-alert-history-heading"
      className="mt-10 grid gap-6"
    >
      <div>
        <h2
          className="text-2xl font-semibold text-white"
          id="in-app-alert-history-heading"
        >
          In-app alert history
        </h2>
        <p className="mt-2 text-sm text-slate-400">
          These alerts remain local to this JobScout installation.
        </p>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <p className="text-sm text-slate-400" role="status">
          {unreadCount} unread {unreadCount === 1 ? "alert" : "alerts"}
        </p>
        <label className="flex items-center gap-2 text-sm text-slate-300">
          <input
            checked={unreadOnly}
            onChange={(event) => changeUnreadFilter(event.target.checked)}
            type="checkbox"
          />
          Show unread only
        </label>
      </div>

      {error ? (
        <p className="notice-error" role="alert">
          {error}
        </p>
      ) : null}

      {loading ? (
        <p aria-live="polite" className="text-slate-400" role="status">
          Loading alert history…
        </p>
      ) : alerts.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-white/15 p-8 text-slate-400">
          {unreadOnly
            ? "No unread alerts."
            : "No alerts yet. Jobs imported when a company is first saved form the baseline and do not alert; matching jobs found by a later poll do."}
        </div>
      ) : (
        alerts.map((alert) => (
          <article
            className="rounded-2xl border border-white/10 bg-slate-950/45 p-5 sm:p-6"
            key={alert.id}
          >
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="text-xs uppercase tracking-wide text-cyan-300">
                  {alert.company_name ?? "Saved company"}
                </p>
                <h3 className="mt-2 text-lg font-semibold text-white">
                  {alert.title}
                </h3>
                <p className="mt-2 text-sm text-slate-400">
                  <time dateTime={alert.created_at}>
                    {new Date(alert.created_at).toLocaleString()}
                  </time>
                </p>
              </div>
              {alert.read_at ? (
                <span className="rounded-full border border-white/15 px-3 py-1 text-xs uppercase text-slate-400">
                  Read
                </span>
              ) : (
                <button
                  className="button-secondary"
                  disabled={markingReadIds.includes(alert.id)}
                  onClick={() => void markRead(alert.id)}
                  type="button"
                >
                  {markingReadIds.includes(alert.id)
                    ? "Marking as read…"
                    : "Mark as read"}
                </button>
              )}
            </div>

            {alert.jobs.length > 0 ? (
              <ul className="mt-5 grid gap-4 border-t border-white/10 pt-4">
                {alert.jobs.map((job) => (
                  <li key={job.job_id}>
                    <p className="text-sm font-medium text-white">
                      {job.event_type === "reopened" ? "Reopened: " : "New: "}
                      {job.title}
                    </p>
                    <p className="mt-1 text-sm text-slate-400">
                      {job.location_text ?? "Location not provided"} · first
                      observed{" "}
                      <time dateTime={job.first_seen_at}>
                        {new Date(job.first_seen_at).toLocaleString()}
                      </time>
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      Why it matched:{" "}
                      {job.match_reasons.join("; ") ||
                        "Matches your saved preferences"}
                    </p>
                    <a
                      className="mt-2 inline-block text-sm text-cyan-300 underline"
                      href={job.apply_url}
                      rel="noreferrer"
                      target="_blank"
                    >
                      Open official posting
                      <span className="sr-only"> (opens in a new tab)</span>
                    </a>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-5 border-t border-white/10 pt-4 text-sm leading-6 text-slate-300">
                {alert.body}
              </p>
            )}

            <div className="mt-5 border-t border-white/10 pt-4">
              <Link className="button-secondary" href={alert.action_url}>
                Open company
              </Link>
            </div>
          </article>
        ))
      )}

      {nextCursor && !loading ? (
        <button
          className="button-secondary justify-self-start"
          disabled={loadingMore}
          onClick={() => void loadMore()}
          type="button"
        >
          {loadingMore ? "Loading older alerts…" : "Load older alerts"}
        </button>
      ) : null}
    </section>
  );
}

function notificationQuery(unreadOnly: boolean, cursor?: string): string {
  const parameters = new URLSearchParams({ limit: "50" });
  if (unreadOnly) parameters.set("unread_only", "true");
  if (cursor) parameters.set("cursor", cursor);
  return `/notifications?${parameters.toString()}`;
}

function mergeUniqueAlerts(
  current: AlertItem[],
  incoming: AlertItem[],
): AlertItem[] {
  const seen = new Set(current.map((alert) => alert.id));
  return [...current, ...incoming.filter((alert) => !seen.has(alert.id))];
}
