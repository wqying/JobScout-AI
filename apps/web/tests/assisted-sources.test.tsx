import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EmailAlertImportPanel } from "@/components/jobs/email-alert-import-panel";
import { SourceRecoveryPanel } from "@/components/jobs/source-recovery-panel";
import { ReviewRemindersPanel } from "@/components/notifications/review-reminders-panel";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    status,
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("monitoring recovery", () => {
  it("previews and confirms a direct ATS replacement before changing the source", async () => {
    const changed = vi.fn(async () => undefined);
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path.endsWith("/source-repairs/preview")) {
          return jsonResponse({
            id: "repair-1",
            provider: "smartrecruiters",
            normalized_url: "https://careers.smartrecruiters.com/acme",
            action: "replace_with_new_source",
            expires_at: "2026-08-30T12:00:00Z",
          });
        }
        if (path.endsWith("/source-repairs/repair-1/confirm")) {
          return jsonResponse({
            id: "repair-1",
            status: "confirmed",
            replacement_source_id: "source-2",
          });
        }
        throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${path}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <SourceRecoveryPanel
        companyId="company-1"
        onSourceChanged={changed}
        savedCompanyId="saved-1"
        source={{
          id: "source-1",
          provider: "generic_html",
          careers_url: "https://acme.example/careers",
          status: "unsupported",
          last_error_code: "ROBOTS_RULE_DISALLOW",
        }}
      />,
    );

    fireEvent.change(
      screen.getByLabelText(
        "Replacement source for https://acme.example/careers",
      ),
      { target: { value: "https://careers.smartrecruiters.com/acme" } },
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Preview replacement" }),
    );

    expect(await screen.findByText("smartrecruiters")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm replacement" }),
    );

    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/companies/company-1/source-repairs/preview",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/source-repairs/repair-1/confirm",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("converts a device-local reminder value into an absolute instant", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path.endsWith("/review-reminders")) {
          const payload = JSON.parse(String(init?.body)) as { due_at: string };
          return jsonResponse({ id: "reminder-1", due_at: payload.due_at });
        }
        throw new Error(`Unexpected request: ${path}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <SourceRecoveryPanel
        companyId="company-1"
        onSourceChanged={async () => undefined}
        savedCompanyId="saved-1"
        source={{
          id: "source-1",
          provider: "unsupported",
          careers_url: "https://acme.example/careers",
          status: "unsupported",
          last_error_code: "ROBOTS_DISALLOWED",
        }}
      />,
    );

    fireEvent.change(screen.getByLabelText("Remind me at"), {
      target: { value: "2026-08-30T09:30" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Schedule reminder" }));

    expect(
      await screen.findByText(/Manual review reminder scheduled/),
    ).toBeInTheDocument();
    const request = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/review-reminders"),
    );
    const body = JSON.parse(String(request?.[1]?.body)) as { due_at: string };
    expect(body.due_at).toBe(new Date("2026-08-30T09:30").toISOString());
  });
});

describe("employer alert imports", () => {
  it("uploads the raw eml file and reports partial import counts", async () => {
    const imported = {
      id: "import-1",
      duplicate: false,
      status: "accepted",
      subject: "New engineering jobs",
      sender: "jobs@acme.example",
      message_id: "message-1",
      content_sha256: "abc123",
      links_found: 2,
      jobs_created: 1,
      jobs_updated: 1,
      created_at: "2026-08-29T12:00:00Z",
    };
    let items: (typeof imported)[] = [];
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (init?.method === "POST") {
          items = [imported];
          return jsonResponse(imported, 201);
        }
        if (path.endsWith("/email-alert-imports")) {
          return jsonResponse({ items });
        }
        throw new Error(`Unexpected request: ${path}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<EmailAlertImportPanel companyId="company-1" />);
    await screen.findByText(/No employer alerts imported/);

    const file = new File(
      ["From: jobs@acme.example\n\nNew jobs"],
      "alert.eml",
      {
        type: "message/rfc822",
      },
    );
    const fileInput = screen.getByLabelText(/Employer alert file/);
    fireEvent.change(fileInput, {
      target: { files: [file] },
    });
    fireEvent.submit(fileInput.closest("form") as HTMLFormElement);

    expect(
      await screen.findByText(/Scanned 2 safe links: 1 new jobs and 1 updated/),
    ).toBeInTheDocument();
    expect(await screen.findByText("New engineering jobs")).toBeInTheDocument();
    const upload = fetchMock.mock.calls.find(
      ([, init]) => init?.method === "POST",
    );
    expect(upload?.[1]?.body).toBe(file);
    expect(new Headers(upload?.[1]?.headers).get("Content-Type")).toBe(
      "message/rfc822",
    );
  });
});

describe("manual review reminders", () => {
  it("shows a due reminder in local time and marks it checked", async () => {
    vi.spyOn(Date, "now").mockReturnValue(
      new Date("2026-08-29T13:00:00Z").getTime(),
    );
    let scheduled = true;
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path.endsWith("/checked") && init?.method === "POST") {
          scheduled = false;
          return jsonResponse({ id: "reminder-1", status: "checked" });
        }
        if (path.includes("/review-reminders?status=scheduled")) {
          return jsonResponse({
            items: scheduled
              ? [
                  {
                    id: "reminder-1",
                    company_id: "company-1",
                    company_name: "Acme Games",
                    career_source_id: "source-1",
                    careers_url: "https://acme.example/careers",
                    due_at: "2026-08-29T12:00:00Z",
                    notified_at: "2026-08-29T12:00:30Z",
                    status: "scheduled",
                  },
                ]
              : [],
          });
        }
        throw new Error(`Unexpected request: ${path}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<ReviewRemindersPanel />);

    expect(await screen.findByText("Review now")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Open careers page" }),
    ).toHaveAttribute("href", "https://acme.example/careers");
    fireEvent.click(screen.getByRole("button", { name: "Mark checked" }));

    await waitFor(() => {
      expect(screen.queryByText("Acme Games")).not.toBeInTheDocument();
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/review-reminders/reminder-1/checked",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
