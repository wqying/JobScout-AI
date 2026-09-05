"use client";

import { useEffect, useState } from "react";

import { ApiError, apiRequest } from "@/lib/api";

export type JobItem = {
  id: string;
  company_id: string;
  company_name: string | null;
  title: string;
  location_text: string | null;
  department: string | null;
  employment_type: string | null;
  role_type: string;
  apply_url: string;
  status: string;
  first_seen_at: string;
  last_seen_at: string;
  closed_at: string | null;
};

export function JobList({ companyId }: { companyId?: string }) {
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const path = companyId ? `/companies/${companyId}/jobs` : "/jobs?limit=100";
    void apiRequest<{ items: JobItem[] }>(path)
      .then((response) => {
        if (active) setJobs(response.items);
      })
      .catch((caught: unknown) => {
        if (active) {
          setError(
            caught instanceof ApiError
              ? caught.message
              : "Could not load jobs.",
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [companyId]);

  if (loading) return <p className="text-slate-400">Loading monitored jobs…</p>;
  if (error)
    return (
      <p className="notice-error" role="alert">
        {error}
      </p>
    );
  if (jobs.length === 0)
    return (
      <div className="rounded-2xl border border-dashed border-white/15 p-8 text-slate-400">
        No jobs have been collected yet. A saved, supported source will be
        eligible for its first poll immediately.
      </div>
    );

  return (
    <div className="grid gap-4">
      {jobs.map((job) => (
        <article
          className="rounded-2xl border border-white/10 bg-slate-950/45 p-5 sm:p-6"
          key={job.id}
        >
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-xs uppercase tracking-wide text-cyan-300">
                {job.company_name ?? job.role_type.replace("_", " ")}
              </p>
              <h2 className="mt-2 text-lg font-semibold text-white">
                {job.title}
              </h2>
              <p className="mt-2 text-sm text-slate-400">
                {job.location_text ?? "Location not provided"}
                {job.department ? ` · ${job.department}` : ""}
              </p>
            </div>
            <span className="rounded-full border border-white/15 px-3 py-1 text-xs uppercase text-slate-300">
              {job.status}
            </span>
          </div>
          <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-white/10 pt-4">
            <p className="text-xs text-slate-500">
              First seen {new Date(job.first_seen_at).toLocaleString()}
            </p>
            <a
              className="button-secondary"
              href={job.apply_url}
              rel="noreferrer"
              target="_blank"
            >
              Open official posting
            </a>
          </div>
        </article>
      ))}
    </div>
  );
}
