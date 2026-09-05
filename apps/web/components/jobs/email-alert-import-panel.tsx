"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";

import { ApiError, apiRequest } from "@/lib/api";

type EmailAlertImport = {
  id: string;
  duplicate: boolean;
  status: string;
  subject: string | null;
  sender: string | null;
  message_id: string | null;
  content_sha256: string;
  links_found: number;
  jobs_created: number;
  jobs_updated: number;
  created_at: string;
};

type ImportList = EmailAlertImport[] | { items: EmailAlertImport[] };

const MAX_EMAIL_BYTES = 1024 * 1024;

export function EmailAlertImportPanel({ companyId }: { companyId: string }) {
  const [imports, setImports] = useState<EmailAlertImport[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadImports = useCallback(async () => {
    try {
      const response = await apiRequest<ImportList>(
        `/companies/${companyId}/email-alert-imports`,
      );
      setImports(Array.isArray(response) ? response : response.items);
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not load imported employer alerts.",
      );
    } finally {
      setLoading(false);
    }
  }, [companyId]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void loadImports(), 0);
    return () => window.clearTimeout(timeout);
  }, [loadImports]);

  async function importEmail(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!file) return;
    if (file.size > MAX_EMAIL_BYTES) {
      setError("Choose an .eml file no larger than 1 MiB.");
      return;
    }

    setWorking(true);
    setError(null);
    setMessage(null);
    try {
      const result = await apiRequest<EmailAlertImport>(
        `/companies/${companyId}/email-alert-imports`,
        {
          method: "POST",
          body: file,
          headers: {
            "Content-Type": "message/rfc822",
            "X-JobScout-Filename": file.name,
          },
        },
      );
      setMessage(
        result.duplicate
          ? "This employer alert was already imported; no jobs or notifications were duplicated."
          : `Scanned ${result.links_found} safe links: ${result.jobs_created} new jobs and ${result.jobs_updated} updated.`,
      );
      setFile(null);
      const input = form.elements.namedItem("eml-file");
      if (input instanceof HTMLInputElement) input.value = "";
      await loadImports();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not import this employer alert.",
      );
    } finally {
      setWorking(false);
    }
  }

  return (
    <section>
      <h2 className="text-2xl font-semibold text-white">
        Import an employer job alert
      </h2>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
        Upload an official <code>.eml</code> message you received after
        subscribing to this employer. JobScout reads it locally, never loads
        embedded resources, and treats every result as partial: imports may add
        or update jobs, but can never close one.
      </p>
      <form
        className="mt-5 grid gap-4 rounded-2xl border border-white/10 bg-white/[0.04] p-5"
        onSubmit={importEmail}
      >
        <label className="grid gap-2 text-sm text-slate-200">
          Employer alert file · maximum 1 MiB
          <input
            accept=".eml,message/rfc822"
            className="input file:mr-4 file:rounded-md file:border-0 file:bg-cyan-300 file:px-3 file:py-1 file:font-medium file:text-slate-950"
            name="eml-file"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            required
            type="file"
          />
        </label>
        <button
          className="button-primary justify-self-start"
          disabled={!file || working}
          type="submit"
        >
          {working ? "Importing…" : "Import alert"}
        </button>
      </form>
      {message ? (
        <p className="notice-success mt-4" role="status">
          {message}
        </p>
      ) : null}
      {error ? (
        <p className="notice-error mt-4" role="alert">
          {error}
        </p>
      ) : null}

      <div className="mt-5 grid gap-3">
        {loading ? (
          <p className="text-sm text-slate-400">Loading imports…</p>
        ) : null}
        {!loading && imports.length === 0 ? (
          <p className="rounded-xl border border-dashed border-white/15 p-5 text-sm text-slate-500">
            No employer alerts imported for this company yet.
          </p>
        ) : null}
        {imports.map((item) => (
          <article
            className="rounded-xl border border-white/10 bg-black/15 p-4 text-sm"
            key={item.id}
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="font-medium text-slate-200">
                  {item.subject || "Employer job alert"}
                </h3>
                <p className="mt-1 text-xs text-slate-500">
                  {item.sender || "Unknown sender"} ·{" "}
                  {new Date(item.created_at).toLocaleString()}
                </p>
              </div>
              <span className="rounded-full border border-white/15 px-3 py-1 text-xs uppercase tracking-wide text-slate-400">
                Partial
              </span>
            </div>
            <p className="mt-3 text-xs text-slate-400">
              {item.links_found} links scanned · {item.jobs_created} new ·{" "}
              {item.jobs_updated} updated
            </p>
          </article>
        ))}
      </div>
    </section>
  );
}
