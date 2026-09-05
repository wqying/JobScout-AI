"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiError, apiRequest } from "@/lib/api";

type EmailConfiguration = {
  requested_mode: "auto" | "fake" | "resend";
  active_adapter: "fake" | "resend" | "unavailable";
  live_delivery_ready: boolean;
  email_notifications_enabled: boolean;
  notification_email_configured: boolean;
  from_address: string | null;
  diagnostic_code: string;
  can_send_test: boolean;
};

export type EmailDelivery = {
  id: string;
  notification_type: "job_alert" | "test_email";
  title: string;
  status: "pending" | "sending" | "sent" | "failed" | "cancelled";
  attempt_count: number;
  next_attempt_at: string | null;
  delivery_adapter: "fake" | "resend" | "unavailable" | null;
  provider_message_id: string | null;
  last_error_code: string | null;
  created_at: string;
  sent_at: string | null;
  retryable: boolean;
};

type EmailDeliveryList = {
  items: EmailDelivery[];
  next_cursor: string | null;
};

const POLL_INTERVAL_MS = 1_000;
const MAX_POLL_ATTEMPTS = 75;

export function EmailDeliveryPanel() {
  const [configuration, setConfiguration] = useState<EmailConfiguration | null>(
    null,
  );
  const [deliveries, setDeliveries] = useState<EmailDelivery[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [configurationLoading, setConfigurationLoading] = useState(true);
  const [deliveriesLoading, setDeliveriesLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [sendingTest, setSendingTest] = useState(false);
  const [retryingIds, setRetryingIds] = useState<string[]>([]);
  const [pollQueue, setPollQueue] = useState<Record<string, number>>({});
  const [configurationError, setConfigurationError] = useState<string | null>(
    null,
  );
  const [deliveryError, setDeliveryError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void Promise.allSettled([
      apiRequest<EmailConfiguration>("/email/configuration"),
      apiRequest<EmailDeliveryList>("/email/deliveries?limit=20"),
    ]).then(([configurationResult, deliveriesResult]) => {
      if (!active) return;

      if (configurationResult.status === "fulfilled") {
        setConfiguration(configurationResult.value);
      } else {
        setConfigurationError(
          errorMessage(
            configurationResult.reason,
            "Could not check the email delivery configuration.",
          ),
        );
      }
      setConfigurationLoading(false);

      if (deliveriesResult.status === "fulfilled") {
        setDeliveries(deliveriesResult.value.items);
        setNextCursor(deliveriesResult.value.next_cursor);
      } else {
        setDeliveryError(
          errorMessage(
            deliveriesResult.reason,
            "Could not load email delivery history.",
          ),
        );
      }
      setDeliveriesLoading(false);
    });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const entries = Object.entries(pollQueue);
    if (entries.length === 0) return;

    let active = true;
    const timer = window.setTimeout(() => {
      void Promise.allSettled(
        entries.map(([id]) =>
          apiRequest<EmailDelivery>(`/email/deliveries/${id}`),
        ),
      ).then((results) => {
        if (!active) return;

        const nextQueue: Record<string, number> = {};
        results.forEach((result, index) => {
          const [id, attempts] = entries[index];
          if (result.status === "rejected") {
            if (attempts + 1 < MAX_POLL_ATTEMPTS) {
              nextQueue[id] = attempts + 1;
            } else {
              setActionMessage(
                "The latest email status could not be refreshed. Its durable queue record is still available below.",
              );
            }
            return;
          }

          const delivery = result.value;
          setDeliveries((current) => upsertDelivery(current, delivery));
          if (delivery.status === "pending" || delivery.status === "sending") {
            if (attempts + 1 < MAX_POLL_ATTEMPTS) {
              nextQueue[id] = attempts + 1;
            } else {
              setActionMessage(
                "The email is still queued. Confirm that the worker and Beat services are running, then reload this page.",
              );
            }
          } else {
            setActionMessage(outcomeMessage(delivery));
          }
        });

        setPollQueue((current) => {
          const merged: Record<string, number> = {};
          for (const [id, attempts] of Object.entries(current)) {
            if (!entries.some(([polledId]) => polledId === id)) {
              merged[id] = attempts;
            }
          }
          return { ...merged, ...nextQueue };
        });
      });
    }, POLL_INTERVAL_MS);

    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [pollQueue]);

  const testInFlight =
    sendingTest ||
    Object.keys(pollQueue).some(
      (id) =>
        deliveries.find((delivery) => delivery.id === id)?.notification_type ===
        "test_email",
    );

  async function sendTestEmail() {
    setSendingTest(true);
    setActionError(null);
    setActionMessage(null);
    try {
      const queued = await apiRequest<EmailDelivery>("/email/test", {
        body: JSON.stringify({ acknowledge_external_email: true }),
        method: "POST",
      });
      setDeliveries((current) => upsertDelivery(current, queued, true));
      setPollQueue((current) => ({ ...current, [queued.id]: 0 }));
      setActionMessage(
        "Test email queued. Waiting for the background worker to contact Resend…",
      );
    } catch (caught) {
      setActionError(errorMessage(caught, "Could not queue the test email."));
    } finally {
      setSendingTest(false);
    }
  }

  async function retryDelivery(delivery: EmailDelivery) {
    setRetryingIds((current) => [...current, delivery.id]);
    setActionError(null);
    setActionMessage(null);
    try {
      const queued = await apiRequest<EmailDelivery>(
        `/email/deliveries/${delivery.id}/retry`,
        { method: "POST" },
      );
      setDeliveries((current) => upsertDelivery(current, queued));
      setPollQueue((current) => ({ ...current, [queued.id]: 0 }));
      setActionMessage(`Queued a retry for “${delivery.title}”.`);
    } catch (caught) {
      setActionError(errorMessage(caught, "Could not retry that email."));
    } finally {
      setRetryingIds((current) => current.filter((id) => id !== delivery.id));
    }
  }

  async function loadMoreDeliveries() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    setDeliveryError(null);
    try {
      const response = await apiRequest<EmailDeliveryList>(
        `/email/deliveries?limit=20&cursor=${encodeURIComponent(nextCursor)}`,
      );
      setDeliveries((current) => mergeUnique(current, response.items));
      setNextCursor(response.next_cursor);
    } catch (caught) {
      setDeliveryError(
        errorMessage(caught, "Could not load older email deliveries."),
      );
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <section
      aria-labelledby="email-delivery-heading"
      className="mt-10 grid gap-6 rounded-2xl border border-white/10 bg-white/[0.04] p-6 sm:p-8"
    >
      <div>
        <p className="eyebrow">Email channel</p>
        <h2
          className="mt-2 text-2xl font-semibold text-white"
          id="email-delivery-heading"
        >
          Email delivery
        </h2>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
          A sent status means Resend accepted the message. Only your inbox can
          confirm final delivery.
        </p>
      </div>

      <div aria-busy={configurationLoading} className="grid gap-3">
        {configurationLoading ? (
          <p className="text-sm text-slate-400" role="status">
            Checking email configuration…
          </p>
        ) : configurationError ? (
          <p className="notice-error" role="alert">
            {configurationError}
          </p>
        ) : configuration ? (
          <div
            aria-live="polite"
            className={configurationClass(configuration)}
            role="status"
          >
            <p className="font-medium">{configurationTitle(configuration)}</p>
            <p className="mt-1">{configurationDescription(configuration)}</p>
            {configuration.from_address ? (
              <p className="mt-1">
                Sender:{" "}
                <span className="font-medium">
                  {configuration.from_address}
                </span>
              </p>
            ) : null}
            <p className="mt-1 text-xs opacity-80">
              Diagnostic: <code>{configuration.diagnostic_code}</code>
            </p>
          </div>
        ) : null}

        {configuration?.can_send_test ? (
          <button
            aria-describedby="test-email-help"
            className="button-primary justify-self-start"
            disabled={testInFlight}
            onClick={() => void sendTestEmail()}
            type="button"
          >
            {sendingTest
              ? "Queueing test email…"
              : testInFlight
                ? "Waiting for test result…"
                : "Send test email"}
          </button>
        ) : configuration ? (
          <Link
            className="button-secondary justify-self-start"
            href="/settings"
          >
            Configure email alerts
          </Link>
        ) : null}
        <p className="text-xs leading-5 text-slate-500" id="test-email-help">
          The test uses your saved notification email and the same durable
          worker path as job alerts. It does not call any AI service.
        </p>
      </div>

      {actionError ? (
        <p className="notice-error" role="alert">
          {actionError}
        </p>
      ) : null}
      {actionMessage ? (
        <p aria-live="polite" className="notice-success" role="status">
          {actionMessage}
        </p>
      ) : null}

      <div className="border-t border-white/10 pt-6">
        <h3 className="text-lg font-semibold text-white">
          Email delivery history
        </h3>
        <p className="mt-2 text-sm text-slate-400">
          Test messages and matching-job emails appear here with their durable
          queue state.
        </p>
      </div>

      <div aria-busy={deliveriesLoading || loadingMore} className="grid gap-4">
        {deliveryError ? (
          <p className="notice-error" role="alert">
            {deliveryError}
          </p>
        ) : null}
        {deliveriesLoading ? (
          <p className="text-sm text-slate-400" role="status">
            Loading email delivery history…
          </p>
        ) : deliveries.length === 0 ? (
          <p className="rounded-xl border border-dashed border-white/15 p-5 text-sm text-slate-400">
            No email deliveries yet.
          </p>
        ) : (
          <ul className="grid gap-4">
            {deliveries.map((delivery) => (
              <li key={delivery.id}>
                <DeliveryCard
                  delivery={delivery}
                  onRetry={retryDelivery}
                  retrying={retryingIds.includes(delivery.id)}
                />
              </li>
            ))}
          </ul>
        )}
        {nextCursor ? (
          <button
            className="button-secondary justify-self-start"
            disabled={loadingMore}
            onClick={() => void loadMoreDeliveries()}
            type="button"
          >
            {loadingMore
              ? "Loading older deliveries…"
              : "Load older email deliveries"}
          </button>
        ) : null}
      </div>
    </section>
  );
}

function DeliveryCard({
  delivery,
  onRetry,
  retrying,
}: {
  delivery: EmailDelivery;
  onRetry: (delivery: EmailDelivery) => Promise<void>;
  retrying: boolean;
}) {
  const presentation = deliveryPresentation(delivery);
  return (
    <article className="rounded-xl border border-white/10 bg-slate-950/45 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-wide text-cyan-300">
            {delivery.notification_type === "test_email"
              ? "Test email"
              : "Job alert email"}
          </p>
          <h4 className="mt-1 font-medium text-white">{delivery.title}</h4>
          <p className="mt-1 text-xs text-slate-500">
            Queued{" "}
            <time dateTime={delivery.created_at}>
              {formatDate(delivery.created_at)}
            </time>
          </p>
        </div>
        <span className={presentation.className}>{presentation.label}</span>
      </div>

      <dl className="mt-4 grid gap-2 text-sm text-slate-400 sm:grid-cols-2">
        <div>
          <dt className="inline text-slate-500">Attempts: </dt>
          <dd className="inline">{delivery.attempt_count}</dd>
        </div>
        {delivery.delivery_adapter ? (
          <div>
            <dt className="inline text-slate-500">Adapter: </dt>
            <dd className="inline">
              {delivery.delivery_adapter === "resend"
                ? "Resend"
                : delivery.delivery_adapter === "fake"
                  ? "Local simulation"
                  : "Unavailable"}
            </dd>
          </div>
        ) : null}
        {delivery.sent_at ? (
          <div>
            <dt className="inline text-slate-500">Provider accepted: </dt>
            <dd className="inline">
              <time dateTime={delivery.sent_at}>
                {formatDate(delivery.sent_at)}
              </time>
            </dd>
          </div>
        ) : null}
        {(delivery.status === "pending" || delivery.status === "sending") &&
        delivery.next_attempt_at ? (
          <div>
            <dt className="inline text-slate-500">Next queue attempt: </dt>
            <dd className="inline">
              <time dateTime={delivery.next_attempt_at}>
                {formatDate(delivery.next_attempt_at)}
              </time>
            </dd>
          </div>
        ) : null}
      </dl>

      {delivery.last_error_code ? (
        <div className="notice-warning mt-4">
          <p>{deliveryErrorDescription(delivery.last_error_code)}</p>
          <p className="mt-1 text-xs opacity-80">
            Error code: <code>{delivery.last_error_code}</code>
          </p>
        </div>
      ) : null}

      {delivery.provider_message_id ? (
        <details className="mt-4 text-xs text-slate-500">
          <summary className="cursor-pointer text-slate-400">
            Provider details
          </summary>
          <p className="mt-2 break-all">
            Message ID: <code>{delivery.provider_message_id}</code>
          </p>
        </details>
      ) : null}

      {delivery.retryable ? (
        <button
          aria-label={`Retry email: ${delivery.title}`}
          className="button-secondary mt-4"
          disabled={retrying}
          onClick={() => void onRetry(delivery)}
          type="button"
        >
          {retrying ? "Queueing retry…" : "Retry email"}
        </button>
      ) : null}
    </article>
  );
}

function configurationTitle(configuration: EmailConfiguration): string {
  if (configuration.can_send_test) return "Resend is ready for live email";
  if (configuration.active_adapter === "fake") {
    return "Local simulation mode—no inbox email";
  }
  if (configuration.live_delivery_ready)
    return "Resend needs your alert settings";
  return "Live email is unavailable";
}

function configurationDescription(configuration: EmailConfiguration): string {
  if (configuration.can_send_test) {
    return "Email alerts are enabled and a notification address is saved.";
  }
  if (configuration.active_adapter === "fake") {
    return "JobScout can exercise its local adapter, but it will not contact Resend or send an email.";
  }
  if (!configuration.email_notifications_enabled) {
    return "Enable email alerts in Settings before sending a test.";
  }
  if (!configuration.notification_email_configured) {
    return "Save a notification email in Settings before sending a test.";
  }
  return "Check the server-side Resend key, sender address, and delivery mode.";
}

function configurationClass(configuration: EmailConfiguration): string {
  if (configuration.can_send_test) return "notice-success";
  if (configuration.active_adapter === "fake") return "notice-warning";
  return "notice-error";
}

function deliveryPresentation(delivery: EmailDelivery): {
  label: string;
  className: string;
} {
  const base = "rounded-full border px-3 py-1 text-xs font-medium";
  if (delivery.status === "pending") {
    return {
      label: "Queued",
      className: `${base} border-amber-400/30 text-amber-200`,
    };
  }
  if (delivery.status === "sending") {
    return {
      label: "Sending",
      className: `${base} border-cyan-400/30 text-cyan-200`,
    };
  }
  if (delivery.status === "failed") {
    return {
      label: "Failed",
      className: `${base} border-rose-400/30 text-rose-200`,
    };
  }
  if (delivery.status === "cancelled") {
    return {
      label: "Cancelled",
      className: `${base} border-slate-400/30 text-slate-300`,
    };
  }
  if (delivery.delivery_adapter === "fake") {
    return {
      label: "Simulated locally—not emailed",
      className: `${base} border-amber-400/30 text-amber-200`,
    };
  }
  if (delivery.delivery_adapter === "resend") {
    return {
      label: "Accepted by Resend",
      className: `${base} border-emerald-400/30 text-emerald-200`,
    };
  }
  return {
    label: "Recorded as sent",
    className: `${base} border-slate-400/30 text-slate-300`,
  };
}

function deliveryErrorDescription(code: string): string {
  const messages: Record<string, string> = {
    EMAIL_NETWORK_ERROR: "JobScout could not reach the email provider.",
    EMAIL_REQUEST_TIMEOUT: "The email provider timed out before responding.",
    EMAIL_RATE_LIMITED: "The provider rate-limited this email.",
    EMAIL_IDEMPOTENCY_CONCURRENT:
      "Another worker is finishing the same provider request; JobScout will retry safely.",
    EMAIL_IDEMPOTENCY_CONFLICT:
      "Resend rejected reuse of an idempotency key with changed message data.",
    EMAIL_PROVIDER_UNAVAILABLE:
      "The email provider was temporarily unavailable.",
    EMAIL_NOTIFICATIONS_DISABLED:
      "Email alerts were disabled before this message was sent.",
    NOTIFICATION_EMAIL_MISSING:
      "No notification email was saved when delivery ran.",
    EMAIL_ADAPTER_NOT_CONFIGURED: "A live email adapter was not configured.",
    RESEND_API_KEY_MISSING: "The Resend API key was missing.",
    EMAIL_FROM_MISSING: "The Resend sender address was missing.",
  };
  if (code.startsWith("EMAIL_REJECTED_")) {
    return "Resend rejected this message or its sender/recipient configuration.";
  }
  return (
    messages[code] ??
    "The email could not be sent. The safe error code is shown below."
  );
}

function outcomeMessage(delivery: EmailDelivery): string {
  const subject =
    delivery.notification_type === "test_email"
      ? "The test email"
      : "The email";
  if (delivery.status === "sent") {
    if (delivery.delivery_adapter === "fake") {
      return `${subject} was simulated locally; no inbox email was sent.`;
    }
    return `${subject} was accepted by Resend. Check your inbox and spam folder.`;
  }
  if (delivery.status === "failed") {
    return `${subject} failed. Review its error and retry option below.`;
  }
  return `${subject} was cancelled. Review its reason and retry option below.`;
}

function upsertDelivery(
  current: EmailDelivery[],
  delivery: EmailDelivery,
  prependIfNew = false,
): EmailDelivery[] {
  const found = current.some((item) => item.id === delivery.id);
  if (!found)
    return prependIfNew ? [delivery, ...current] : [...current, delivery];
  return current.map((item) => (item.id === delivery.id ? delivery : item));
}

function mergeUnique(
  current: EmailDelivery[],
  incoming: EmailDelivery[],
): EmailDelivery[] {
  const seen = new Set(current.map((delivery) => delivery.id));
  return [...current, ...incoming.filter((delivery) => !seen.has(delivery.id))];
}

function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? caught.message : fallback;
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}
