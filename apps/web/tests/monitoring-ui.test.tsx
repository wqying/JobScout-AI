import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CompanyDetailView } from "@/components/jobs/company-detail";
import { JobList } from "@/components/jobs/job-list";

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    status: 200,
  });
}

const job = {
  id: "job-1",
  company_id: "company-1",
  company_name: "Acme Games",
  title: "Software Engineer Intern",
  location_text: "New York, NY",
  department: "Engineering",
  employment_type: "Intern",
  role_type: "internship",
  apply_url: "https://jobs.example.com/job-1",
  status: "active",
  first_seen_at: "2026-08-27T12:00:00Z",
  last_seen_at: "2026-08-27T13:00:00Z",
  closed_at: null,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("monitoring views", () => {
  it("shows jobs collected from saved companies", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ items: [job], next_cursor: null })),
    );

    render(<JobList />);

    expect(
      await screen.findByRole("heading", { name: job.title }),
    ).toBeInTheDocument();
    expect(screen.getByText("Acme Games")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Open official posting" }),
    ).toHaveAttribute("href", job.apply_url);
  });

  it("shows source status, failures, and company jobs together", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path.endsWith("/companies/company-1")) {
          return jsonResponse({
            id: "company-1",
            canonical_name: "Acme Games",
            sources: [
              {
                id: "source-1",
                provider: "lever",
                careers_url: "https://jobs.lever.co/acme",
                status: "degraded",
                last_success_at: "2026-08-27T12:00:00Z",
                next_poll_at: "2026-08-27T14:00:00Z",
                consecutive_failures: 2,
                last_error_code: "SOURCE_TIMEOUT",
              },
            ],
          });
        }
        if (path.endsWith("/companies/company-1/jobs")) {
          return jsonResponse({ items: [job], next_cursor: null });
        }
        if (path.endsWith("/companies/company-1/email-alert-imports")) {
          return jsonResponse({ items: [] });
        }
        if (
          path.endsWith("/ops/sources/source-1/poll") &&
          init?.method === "POST"
        ) {
          return jsonResponse({
            source_id: "source-1",
            status: "queued",
            message: "The source poll was queued.",
          });
        }
        throw new Error(`Unexpected request: ${path}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<CompanyDetailView companyId="company-1" />);

    expect(
      await screen.findByRole("heading", { name: "Acme Games" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(/2 consecutive failures/i),
    ).toHaveTextContent("SOURCE_TIMEOUT");
    expect(
      await screen.findByRole("heading", { name: job.title }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Poll now" }));
    expect(
      await screen.findByText(/new matching jobs will create alerts/i),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/ops/sources/source-1/poll",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });
});
