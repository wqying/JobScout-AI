"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { ApiError, apiRequest } from "@/lib/api";

type Profile = {
  display_name: string;
  email: string | null;
  country_code: string;
};

type Settings = {
  role_types: string[];
  keywords: string[];
  excluded_keywords: string[];
  preferred_locations: string[];
  remote_preference: string;
  notify_current_jobs_on_save: boolean;
  email_notifications_enabled: boolean;
  notification_email: string | null;
};

const defaultRoles = ["internship", "new_grad", "entry_level"];

export function ProfileSettingsForm({
  mode,
}: {
  mode: "onboarding" | "settings";
}) {
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [roles, setRoles] = useState<string[]>(defaultRoles);
  const [keywords, setKeywords] = useState("");
  const [excludedKeywords, setExcludedKeywords] = useState("");
  const [locations, setLocations] = useState("");
  const [remotePreference, setRemotePreference] = useState("any");
  const [notifyCurrentJobsOnSave, setNotifyCurrentJobsOnSave] = useState(false);
  const [emailAlertsEnabled, setEmailAlertsEnabled] = useState(false);
  const [notificationEmail, setNotificationEmail] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    Promise.allSettled([
      apiRequest<Profile>("/profile"),
      apiRequest<Settings>("/settings"),
    ]).then(([profileResult, settingsResult]) => {
      if (!active) return;
      if (profileResult.status === "fulfilled") {
        setDisplayName(profileResult.value.display_name);
        setEmail(profileResult.value.email ?? "");
      }
      if (settingsResult.status === "fulfilled") {
        const settings = settingsResult.value;
        setRoles(settings.role_types);
        setKeywords(settings.keywords.join(", "));
        setExcludedKeywords(settings.excluded_keywords.join(", "));
        setLocations(settings.preferred_locations.join(", "));
        setRemotePreference(settings.remote_preference);
        setNotifyCurrentJobsOnSave(settings.notify_current_jobs_on_save);
        setEmailAlertsEnabled(settings.email_notifications_enabled);
        setNotificationEmail(settings.notification_email ?? "");
      }
      setLoading(false);
    });
    return () => {
      active = false;
    };
  }, []);

  function toggleRole(role: string) {
    setRoles((current) =>
      current.includes(role)
        ? current.filter((value) => value !== role)
        : [...current, role],
    );
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await apiRequest<Profile>("/profile", {
        method: "PUT",
        body: JSON.stringify({
          display_name: displayName,
          email: email || null,
          country_code: "US",
        }),
      });
      await apiRequest<Settings>("/settings", {
        method: "PUT",
        body: JSON.stringify({
          role_types: roles,
          keywords: splitList(keywords),
          excluded_keywords: splitList(excludedKeywords),
          preferred_locations: splitList(locations),
          remote_preference: remotePreference,
          notify_current_jobs_on_save: notifyCurrentJobsOnSave,
          email_notifications_enabled: emailAlertsEnabled,
          notification_email: notificationEmail || null,
        }),
      });
      setMessage(
        mode === "onboarding" ? "Local profile created." : "Preferences saved.",
      );
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not save your local profile.",
      );
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <p className="py-16 text-slate-400">Loading local preferences…</p>;
  }

  return (
    <form className="mt-10 grid gap-8" onSubmit={submit}>
      <section className="grid gap-5 rounded-2xl border border-white/10 bg-white/[0.04] p-6 sm:p-8">
        <div>
          <h2 className="text-xl font-semibold text-white">
            Your local profile
          </h2>
          <p className="mt-2 text-sm text-slate-400">
            Stored only in this installation.
          </p>
        </div>
        <label className="grid gap-2 text-sm text-slate-200">
          Display name
          <input
            className="input"
            maxLength={120}
            onChange={(event) => setDisplayName(event.target.value)}
            required
            value={displayName}
          />
        </label>
        <label className="grid gap-2 text-sm text-slate-200">
          Email (optional)
          <input
            className="input"
            maxLength={254}
            onChange={(event) => setEmail(event.target.value)}
            type="email"
            value={email}
          />
        </label>
      </section>

      <section className="grid gap-5 rounded-2xl border border-white/10 bg-white/[0.04] p-6 sm:p-8">
        <div>
          <h2 className="text-xl font-semibold text-white">Job preferences</h2>
          <p className="mt-2 text-sm text-slate-400">
            These deterministic filters decide which newly discovered jobs
            create an alert. No AI call is spent per polled job.
          </p>
        </div>
        <fieldset>
          <legend className="mb-3 text-sm text-slate-200">Role types</legend>
          <div className="flex flex-wrap gap-4">
            {defaultRoles.map((role) => (
              <label
                className="flex items-center gap-2 text-sm text-slate-300"
                key={role}
              >
                <input
                  checked={roles.includes(role)}
                  onChange={() => toggleRole(role)}
                  type="checkbox"
                />
                {role.replace("_", " ")}
              </label>
            ))}
          </div>
        </fieldset>
        <TextList
          label="Required keywords"
          onChange={setKeywords}
          value={keywords}
        />
        <TextList
          label="Excluded keywords"
          onChange={setExcludedKeywords}
          value={excludedKeywords}
        />
        <TextList
          label="Preferred locations"
          onChange={setLocations}
          value={locations}
        />
        <label className="grid gap-2 text-sm text-slate-200">
          Work arrangement
          <select
            className="input"
            onChange={(event) => setRemotePreference(event.target.value)}
            value={remotePreference}
          >
            <option value="any">Any</option>
            <option value="remote">Remote</option>
            <option value="hybrid">Hybrid</option>
            <option value="onsite">On-site</option>
          </select>
        </label>
      </section>

      <section className="grid gap-5 rounded-2xl border border-white/10 bg-white/[0.04] p-6 sm:p-8">
        <div>
          <h2 className="text-xl font-semibold text-white">Alerts</h2>
          <p className="mt-2 text-sm text-slate-400">
            In-app alerts are always recorded for matching jobs. Email delivery
            is optional and sent from this installation only.
          </p>
        </div>
        <label className="flex items-start gap-3 text-sm text-slate-300">
          <input
            checked={notifyCurrentJobsOnSave}
            className="mt-1"
            onChange={(event) =>
              setNotifyCurrentJobsOnSave(event.target.checked)
            }
            type="checkbox"
          />
          <span>
            Notify me about currently open jobs
            <span className="mt-1 block text-slate-500">
              Off by default: jobs imported when a company is first saved form
              the alert baseline instead of alerting.
            </span>
          </span>
        </label>
        <label className="flex items-start gap-3 text-sm text-slate-300">
          <input
            checked={emailAlertsEnabled}
            className="mt-1"
            onChange={(event) => setEmailAlertsEnabled(event.target.checked)}
            type="checkbox"
          />
          <span>Send me email alerts</span>
        </label>
        <label className="grid gap-2 text-sm text-slate-200">
          Notification email
          <input
            aria-describedby="notification-email-help"
            className="input"
            maxLength={254}
            onChange={(event) => setNotificationEmail(event.target.value)}
            required={emailAlertsEnabled}
            type="email"
            value={notificationEmail}
          />
          <span className="text-xs text-slate-500" id="notification-email-help">
            Required when email alerts are enabled. Without configured Resend
            credentials, JobScout records deliveries locally instead of sending.
          </span>
        </label>
      </section>

      {error ? (
        <p className="notice-error" role="alert">
          {error}
        </p>
      ) : null}
      {message ? (
        <div className="notice-success" role="status">
          {message}{" "}
          {mode === "onboarding" ? (
            <Link className="underline" href="/companies">
              Add a company
            </Link>
          ) : null}
        </div>
      ) : null}
      <button
        className="button-primary justify-self-start"
        disabled={
          saving ||
          roles.length === 0 ||
          (emailAlertsEnabled && notificationEmail.trim() === "")
        }
        type="submit"
      >
        {saving
          ? "Saving…"
          : mode === "onboarding"
            ? "Create local profile"
            : "Save preferences"}
      </button>
    </form>
  );
}

function TextList({
  label,
  onChange,
  value,
}: {
  label: string;
  onChange: (value: string) => void;
  value: string;
}) {
  return (
    <label className="grid gap-2 text-sm text-slate-200">
      {label}
      <input
        className="input"
        onChange={(event) => onChange(event.target.value)}
        placeholder="Comma-separated"
        value={value}
      />
    </label>
  );
}

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}
