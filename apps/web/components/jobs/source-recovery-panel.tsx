"use client";

import { useState } from "react";

import { ApiError, apiRequest } from "@/lib/api";

export type RecoverableSource = {
  id: string;
  provider: string;
  careers_url: string;
  status: string;
  last_error_code: string | null;
};

type RepairPreview = {
  id: string;
  provider: string;
  normalized_url: string;
  action: string;
  expires_at: string;
};

type Reminder = {
  id: string;
  due_at: string;
};

export function monitoringMode(source: RecoverableSource) {
  if (source.status === "retired") return "Retired history";
  if (source.provider === "email_alert") return "Assisted";
  if (source.provider === "unsupported" || source.status === "unsupported") {
    return "Reminder only";
  }
  if (source.provider === "generic_html") return "Automatic · partial";
  return "Automatic · structured";
}

export function sourceDiagnostic(code: string | null) {
  switch (code) {
    case "ROBOTS_TXT_HTTP_401":
    case "ROBOTS_TXT_HTTP_403":
      return "The site blocked access to its robots policy. JobScout will not try to bypass it; use a documented ATS board URL or a manual reminder.";
    case "ROBOTS_FETCH_FAILED":
      return "JobScout could not retrieve the site's robots policy. This is retryable and will use the normal backoff schedule.";
    case "ROBOTS_DISALLOWED":
      return "This source is blocked by its robots policy. JobScout will not bypass it.";
    case "NO_SERVER_RENDERED_JOB_LINKS":
      return "No stable job links were present in the server-rendered page. Try the employer's direct ATS board URL.";
    case "URL_DNS_FAILED":
      return "The hostname could not be resolved. DNS failures are retryable; you can also replace this with a direct ATS board URL.";
    default:
      return code;
  }
}

export function SourceRecoveryPanel({
  companyId,
  onSourceChanged,
  savedCompanyId,
  source,
}: {
  companyId: string;
  onSourceChanged: () => Promise<void>;
  savedCompanyId: string | null;
  source: RecoverableSource;
}) {
  const [repairUrl, setRepairUrl] = useState("");
  const [notifyCurrentJobs, setNotifyCurrentJobs] = useState(false);
  const [preview, setPreview] = useState<RepairPreview | null>(null);
  const [reminderAt, setReminderAt] = useState(defaultReminderValue);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function previewRepair() {
    setWorking(true);
    setError(null);
    setMessage(null);
    try {
      const response = await apiRequest<RepairPreview>(
        `/companies/${companyId}/source-repairs/preview`,
        {
          method: "POST",
          body: JSON.stringify({
            source_id: source.id,
            careers_url: repairUrl,
            notify_current_jobs: notifyCurrentJobs,
          }),
        },
      );
      setPreview(response);
    } catch (caught) {
      setError(
        errorMessage(caught, "Could not preview this replacement source."),
      );
    } finally {
      setWorking(false);
    }
  }

  async function confirmRepair() {
    if (!preview) return;
    setWorking(true);
    setError(null);
    try {
      await apiRequest(`/source-repairs/${preview.id}/confirm`, {
        method: "POST",
      });
      setPreview(null);
      setRepairUrl("");
      setMessage("The replacement source is scheduled for its first poll.");
      await onSourceChanged();
    } catch (caught) {
      setError(
        errorMessage(caught, "Could not confirm this replacement source."),
      );
    } finally {
      setWorking(false);
    }
  }

  async function scheduleReminder() {
    if (!savedCompanyId) return;
    const dueAt = new Date(reminderAt);
    if (Number.isNaN(dueAt.getTime())) {
      setError("Choose a valid reminder date and time.");
      return;
    }
    setWorking(true);
    setError(null);
    setMessage(null);
    try {
      const reminder = await apiRequest<Reminder>(
        `/saved-companies/${savedCompanyId}/review-reminders`,
        {
          method: "POST",
          body: JSON.stringify({
            career_source_id: source.id,
            due_at: dueAt.toISOString(),
          }),
        },
      );
      setMessage(
        `Manual review reminder scheduled for ${new Date(reminder.due_at).toLocaleString()}.`,
      );
    } catch (caught) {
      setError(errorMessage(caught, "Could not schedule this reminder."));
    } finally {
      setWorking(false);
    }
  }

  const canRepair = source.provider !== "email_alert";

  return (
    <div className="mt-5 grid gap-5 border-t border-white/10 pt-5">
      {message ? (
        <p className="notice-success" role="status">
          {message}
        </p>
      ) : null}
      {error ? (
        <p className="notice-error" role="alert">
          {error}
        </p>
      ) : null}

      {canRepair ? (
        <form
          className="grid gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void previewRepair();
          }}
        >
          <div>
            <h4 className="text-sm font-semibold text-slate-200">
              Replace monitoring source
            </h4>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              Paste a direct Greenhouse, Lever, Ashby, or SmartRecruiters board
              URL. JobScout previews the provider before changing anything.
            </p>
          </div>
          <input
            aria-label={`Replacement source for ${source.careers_url}`}
            className="input"
            onChange={(event) => {
              setRepairUrl(event.target.value);
              setPreview(null);
            }}
            placeholder="https://jobs.lever.co/company"
            required
            type="url"
            value={repairUrl}
          />
          <label className="flex items-start gap-3 text-xs leading-5 text-slate-400">
            <input
              checked={notifyCurrentJobs}
              className="mt-1"
              onChange={(event) => {
                setNotifyCurrentJobs(event.target.checked);
                setPreview(null);
              }}
              type="checkbox"
            />
            Alert for matching jobs already present on the replacement
            source&apos;s first poll. Leave off to establish a quiet baseline.
          </label>
          {preview ? (
            <div className="notice-warning grid gap-3">
              <p>
                Detected{" "}
                <strong className="capitalize">{preview.provider}</strong> ·{" "}
                {preview.action.replaceAll("_", " ")}
              </p>
              <p className="break-all text-xs">{preview.normalized_url}</p>
              <button
                className="button-primary justify-self-start"
                disabled={working}
                onClick={() => void confirmRepair()}
                type="button"
              >
                {working ? "Confirming…" : "Confirm replacement"}
              </button>
            </div>
          ) : (
            <button
              className="button-secondary justify-self-start"
              disabled={working}
              type="submit"
            >
              {working ? "Checking…" : "Preview replacement"}
            </button>
          )}
        </form>
      ) : null}

      {savedCompanyId ? (
        <form
          className="grid gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void scheduleReminder();
          }}
        >
          <div>
            <h4 className="text-sm font-semibold text-slate-200">
              Manual review reminder
            </h4>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              The date and time below use this device&apos;s local clock.
            </p>
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <label className="grid min-w-64 flex-1 gap-2 text-xs text-slate-400">
              Remind me at
              <input
                className="input"
                onChange={(event) => setReminderAt(event.target.value)}
                required
                type="datetime-local"
                value={reminderAt}
              />
            </label>
            <button
              className="button-secondary"
              disabled={working}
              type="submit"
            >
              {working ? "Saving…" : "Schedule reminder"}
            </button>
          </div>
        </form>
      ) : null}
    </div>
  );
}

function defaultReminderValue() {
  const tomorrow = new Date(Date.now() + 24 * 60 * 60 * 1000);
  tomorrow.setSeconds(0, 0);
  const local = new Date(
    tomorrow.getTime() - tomorrow.getTimezoneOffset() * 60 * 1000,
  );
  return local.toISOString().slice(0, 16);
}

function errorMessage(caught: unknown, fallback: string) {
  return caught instanceof ApiError ? caught.message : fallback;
}
