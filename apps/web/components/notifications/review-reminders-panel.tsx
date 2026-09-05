"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ApiError, apiRequest } from "@/lib/api";

type ReviewReminder = {
  id: string;
  company_id: string;
  company_name: string;
  career_source_id: string | null;
  careers_url: string | null;
  due_at: string;
  notified_at: string | null;
  status: "scheduled" | "checked" | "dismissed";
};

type ReminderList = ReviewReminder[] | { items: ReviewReminder[] };

export function ReviewRemindersPanel() {
  const [reminders, setReminders] = useState<ReviewReminder[]>([]);
  const [now, setNow] = useState(() => Date.now());
  const [loading, setLoading] = useState(true);
  const [workingId, setWorkingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadReminders = useCallback(async () => {
    try {
      const response = await apiRequest<ReminderList>(
        "/review-reminders?status=scheduled",
      );
      setReminders(Array.isArray(response) ? response : response.items);
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not load manual-review reminders.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => void loadReminders(), 0);
    const interval = window.setInterval(() => {
      setNow(Date.now());
      void loadReminders();
    }, 60_000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(interval);
    };
  }, [loadReminders]);

  async function complete(reminderId: string, action: "checked" | "dismiss") {
    setWorkingId(reminderId);
    setError(null);
    try {
      await apiRequest(`/review-reminders/${reminderId}/${action}`, {
        method: "POST",
      });
      setReminders((current) =>
        current.filter((reminder) => reminder.id !== reminderId),
      );
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not update this reminder.",
      );
    } finally {
      setWorkingId(null);
    }
  }

  return (
    <section className="mt-10">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Reminder-only sources</p>
          <h2 className="mt-3 text-2xl font-semibold text-white">
            Manual careers-page reviews
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
            Due times are shown using this device&apos;s clock. JobScout checks
            for due reminders every minute while the local services are running.
          </p>
        </div>
        <Link
          className="text-sm text-cyan-300 hover:underline"
          href="/companies"
        >
          Manage saved companies
        </Link>
      </div>
      {error ? (
        <p className="notice-error mt-4" role="alert">
          {error}
        </p>
      ) : null}
      {loading ? (
        <p className="mt-5 text-sm text-slate-400">Loading reminders…</p>
      ) : null}
      {!loading && reminders.length === 0 ? (
        <p className="mt-5 rounded-xl border border-dashed border-white/15 p-5 text-sm text-slate-500">
          No manual reviews are scheduled.
        </p>
      ) : null}
      <div className="mt-5 grid gap-4">
        {reminders.map((reminder) => {
          const isDue = new Date(reminder.due_at).getTime() <= now;
          return (
            <article
              className="rounded-2xl border border-white/10 bg-white/[0.04] p-5"
              key={reminder.id}
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h3 className="font-semibold text-white">
                    {reminder.company_name}
                  </h3>
                  <p className="mt-1 text-sm text-slate-400">
                    {isDue ? "Due" : "Scheduled for"}{" "}
                    {new Date(reminder.due_at).toLocaleString()}
                  </p>
                </div>
                <span
                  className={
                    isDue
                      ? "rounded-full border border-amber-300/30 bg-amber-300/10 px-3 py-1 text-xs uppercase tracking-wide text-amber-100"
                      : "rounded-full border border-white/15 px-3 py-1 text-xs uppercase tracking-wide text-slate-400"
                  }
                >
                  {isDue ? "Review now" : "Upcoming"}
                </span>
              </div>
              <div className="mt-4 flex flex-wrap gap-3">
                {reminder.careers_url ? (
                  <a
                    className="button-secondary"
                    href={reminder.careers_url}
                    rel="noreferrer"
                    target="_blank"
                  >
                    Open careers page
                  </a>
                ) : null}
                <button
                  className="button-primary"
                  disabled={workingId !== null}
                  onClick={() => void complete(reminder.id, "checked")}
                  type="button"
                >
                  {workingId === reminder.id ? "Saving…" : "Mark checked"}
                </button>
                <button
                  className="button-secondary"
                  disabled={workingId !== null}
                  onClick={() => void complete(reminder.id, "dismiss")}
                  type="button"
                >
                  Dismiss
                </button>
                <Link
                  className="button-secondary"
                  href={`/companies/${reminder.company_id}`}
                >
                  Company details
                </Link>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
