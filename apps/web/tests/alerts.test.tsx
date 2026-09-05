import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AlertHistory } from "@/components/notifications/alert-history";
import { EmailDeliveryPanel } from "@/components/notifications/email-delivery-panel";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    status,
  });
}

const alert = {
  id: "alert-1",
  outbox_id: "outbox-1",
  title: "2 matching Acme Games openings",
  body: "New: Software Engineer Intern — New York, NY",
  action_url: "/companies/company-1",
  read_at: null,
  created_at: "2026-08-27T13:00:00Z",
  company_id: "company-1",
  company_name: "Acme Games",
  jobs: [
    {
      job_id: "job-1",
      title: "Software Engineer Intern",
      location_text: "New York, NY",
      role_type: "internship",
      apply_url: "https://jobs.example.com/job-1",
      event_type: "discovered" as const,
      first_seen_at: "2026-08-27T12:00:00Z",
      match_reasons: ["Role type: internship", "Keyword match: python"],
    },
    {
      job_id: "job-2",
      title: "Data Analyst, New Grad",
      location_text: "Remote, US",
      role_type: "new_grad",
      apply_url: "https://jobs.example.com/job-2",
      event_type: "reopened" as const,
      first_seen_at: "2026-08-26T12:00:00Z",
      match_reasons: ["Role type: new grad"],
    },
  ],
};

const readyConfiguration = {
  requested_mode: "auto" as const,
  active_adapter: "resend" as const,
  live_delivery_ready: true,
  email_notifications_enabled: true,
  notification_email_configured: true,
  from_address: "JobScout AI <alerts@example.com>",
  diagnostic_code: "READY_TO_SEND",
  can_send_test: true,
};

const queuedTestDelivery = {
  id: "delivery-test-1",
  notification_type: "test_email" as const,
  title: "Your JobScout AI email alerts are configured",
  status: "pending" as const,
  attempt_count: 0,
  next_attempt_at: "2026-08-27T13:01:00Z",
  delivery_adapter: null,
  provider_message_id: null,
  last_error_code: null,
  created_at: "2026-08-27T13:00:00Z",
  sent_at: null,
  retryable: false,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("alert history", () => {
  it("lists grouped alerts with the reasons each job matched", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ items: [alert], next_cursor: null, unread_count: 1 }),
      ),
    );

    render(<AlertHistory />);

    expect(
      await screen.findByRole("heading", { name: alert.title }),
    ).toBeInTheDocument();
    expect(screen.getByText("Acme Games")).toBeInTheDocument();
    expect(screen.getByText("1 unread alert")).toBeInTheDocument();
    expect(
      screen.getByText(/New: Software Engineer Intern/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Reopened: Data Analyst/)).toBeInTheDocument();
    expect(
      screen.getByText(/Role type: internship; Keyword match: python/),
    ).toBeInTheDocument();
  });

  it("explains the baseline rule when nothing has alerted yet", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ items: [], next_cursor: null, unread_count: 0 }),
      ),
    );

    render(<AlertHistory />);

    expect(await screen.findByText(/No alerts yet/)).toHaveTextContent(
      /baseline and do not alert/,
    );
  });

  it("shows the explanatory body for a local source-health alert", async () => {
    const sourceAlert = {
      ...alert,
      id: "source-alert-1",
      title: "Monitoring degraded for Acme Games",
      body: "The careers source failed 10 consecutive polls. Latest error: SOURCE_TIMEOUT.",
      jobs: [],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          items: [sourceAlert],
          next_cursor: null,
          unread_count: 1,
        }),
      ),
    );

    render(<AlertHistory />);

    expect(
      await screen.findByRole("heading", { name: sourceAlert.title }),
    ).toBeInTheDocument();
    expect(screen.getByText(sourceAlert.body)).toBeInTheDocument();
  });

  it("marks one alert as read without reloading the page", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "POST") {
          return jsonResponse({ ...alert, read_at: "2026-08-27T14:00:00Z" });
        }
        return jsonResponse({
          items: [alert],
          next_cursor: null,
          unread_count: 1,
        });
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<AlertHistory />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Mark as read" }),
    );

    await waitFor(() => {
      expect(screen.getByText("Read")).toBeInTheDocument();
    });
    expect(screen.getByText("0 unread alerts")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/notifications/alert-1/read",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("loads older alerts and preserves the unread filter in pagination requests", async () => {
    const olderAlert = {
      ...alert,
      id: "alert-2",
      outbox_id: "outbox-2",
      title: "1 matching Older Company opening",
      company_name: "Older Company",
      created_at: "2026-08-26T13:00:00Z",
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.includes("cursor=alert-1")) {
        return jsonResponse({
          items: [olderAlert],
          next_cursor: null,
          unread_count: 2,
        });
      }
      if (path.includes("unread_only=true")) {
        return jsonResponse({
          items: [olderAlert],
          next_cursor: null,
          unread_count: 1,
        });
      }
      return jsonResponse({
        items: [alert],
        next_cursor: "alert-1",
        unread_count: 2,
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AlertHistory />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Load older alerts" }),
    );

    expect(
      await screen.findByRole("heading", { name: olderAlert.title }),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("cursor=alert-1"),
      expect.anything(),
    );

    fireEvent.click(screen.getByRole("checkbox", { name: "Show unread only" }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("unread_only=true"),
        expect.anything(),
      );
    });
    expect(screen.queryByText("Load older alerts")).not.toBeInTheDocument();
  });
});

describe("email delivery panel", () => {
  it("queues a real test and polls until Resend accepts it", async () => {
    const accepted = {
      ...queuedTestDelivery,
      status: "sent" as const,
      attempt_count: 1,
      delivery_adapter: "resend" as const,
      provider_message_id: "resend-message-1",
      sent_at: "2026-08-27T13:00:05Z",
    };
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        const method = init?.method ?? "GET";
        if (path.endsWith("/email/configuration")) {
          return jsonResponse(readyConfiguration);
        }
        if (path.endsWith("/email/deliveries?limit=20")) {
          return jsonResponse({ items: [], next_cursor: null });
        }
        if (path.endsWith("/email/test") && method === "POST") {
          return jsonResponse(queuedTestDelivery, 202);
        }
        if (path.endsWith(`/email/deliveries/${queuedTestDelivery.id}`)) {
          return jsonResponse(accepted);
        }
        throw new Error(`Unexpected request: ${method} ${path}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<EmailDeliveryPanel />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Send test email" }),
    );

    expect(await screen.findByText(/Test email queued/)).toBeInTheDocument();
    expect(
      await screen.findByText("Accepted by Resend", {}, { timeout: 2_500 }),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/email/test",
      expect.objectContaining({
        body: JSON.stringify({ acknowledge_external_email: true }),
        method: "POST",
      }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/email/deliveries/${queuedTestDelivery.id}`,
      expect.anything(),
    );
  });

  it("truthfully identifies fake mode and does not offer a real test button", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = String(input);
        if (path.endsWith("/email/configuration")) {
          return jsonResponse({
            ...readyConfiguration,
            requested_mode: "fake",
            active_adapter: "fake",
            live_delivery_ready: false,
            from_address: null,
            diagnostic_code: "FAKE_EMAIL_MODE",
            can_send_test: false,
          });
        }
        return jsonResponse({ items: [], next_cursor: null });
      }),
    );

    render(<EmailDeliveryPanel />);

    expect(
      await screen.findByText(/Local simulation mode/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/will not contact Resend or send an email/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Send test email" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Configure email alerts" }),
    ).toHaveAttribute("href", "/settings");
  });

  it("retries failed email and loads older delivery history", async () => {
    const failed = {
      ...queuedTestDelivery,
      id: "delivery-failed-1",
      notification_type: "job_alert" as const,
      title: "1 matching Acme Games opening",
      status: "failed" as const,
      attempt_count: 5,
      last_error_code: "EMAIL_PROVIDER_UNAVAILABLE",
      retryable: true,
    };
    const older = {
      ...queuedTestDelivery,
      id: "delivery-older-1",
      title: "Older test email",
      status: "sent" as const,
      attempt_count: 1,
      delivery_adapter: "fake" as const,
      provider_message_id: "fake-email-1",
      sent_at: "2026-08-26T13:00:05Z",
    };
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        const method = init?.method ?? "GET";
        if (path.endsWith("/email/configuration")) {
          return jsonResponse(readyConfiguration);
        }
        if (path.endsWith("/email/deliveries?limit=20")) {
          return jsonResponse({ items: [failed], next_cursor: failed.id });
        }
        if (path.includes(`cursor=${failed.id}`)) {
          return jsonResponse({ items: [older], next_cursor: null });
        }
        if (
          path.endsWith(`/email/deliveries/${failed.id}/retry`) &&
          method === "POST"
        ) {
          return jsonResponse({
            ...failed,
            status: "pending",
            attempt_count: 0,
            last_error_code: null,
            retryable: false,
          });
        }
        throw new Error(`Unexpected request: ${method} ${path}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<EmailDeliveryPanel />);
    fireEvent.click(
      await screen.findByRole("button", {
        name: `Retry email: ${failed.title}`,
      }),
    );

    expect(await screen.findByText(/Queued a retry/)).toBeInTheDocument();
    expect(screen.getAllByText("Queued")).not.toHaveLength(0);
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/email/deliveries/${failed.id}/retry`,
      expect.objectContaining({ method: "POST" }),
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Load older email deliveries" }),
    );
    expect(await screen.findByText(older.title)).toBeInTheDocument();
    expect(
      screen.getByText("Simulated locally—not emailed"),
    ).toBeInTheDocument();
  });
});
