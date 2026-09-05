import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CompanyManager } from "@/components/forms/company-manager";

const proposal = {
  id: "company-1",
  canonical_name: "Acme Games",
  verification_status: "proposed",
  is_saved: false,
  saved_company_id: null,
  saved_status: null,
  sources: [
    {
      id: "source-1",
      provider: "lever",
      careers_url: "https://jobs.lever.co/acme",
      status: "pending",
      last_success_at: null,
    },
  ],
  sponsorship: {
    status: "no_records",
    certified_cases: 0,
    certified_workers: 0,
    loaded_fiscal_years: [],
    explanation: "No verified legal-entity mapping exists yet.",
  },
  warning: null,
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    status,
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("manual company addition", () => {
  it("does not save a resolved proposal until the owner confirms", async () => {
    let saved = false;
    const requests: Array<{
      body: string | null;
      method: string;
      path: string;
    }> = [];
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        const method = init?.method ?? "GET";
        requests.push({
          body: typeof init?.body === "string" ? init.body : null,
          method,
          path,
        });

        if (path.includes("/companies?limit=100") && method === "GET") {
          return jsonResponse({
            items: saved
              ? [
                  {
                    ...proposal,
                    is_saved: true,
                    saved_company_id: "saved-1",
                    saved_status: "active",
                    notify_current_jobs: true,
                    baseline_completed: false,
                    verification_status: "owner_verified",
                  },
                ]
              : [],
            next_cursor: null,
          });
        }
        if (path.endsWith("/companies/resolve") && method === "POST") {
          return jsonResponse({
            resolution: "proposal",
            companies: [proposal],
            message: "Review this proposal before saving.",
          });
        }
        if (path.endsWith("/companies/company-1/save") && method === "POST") {
          saved = true;
          return jsonResponse({
            ...proposal,
            is_saved: true,
            saved_company_id: "saved-1",
          });
        }
        throw new Error(`Unexpected request: ${method} ${path}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<CompanyManager />);
    await screen.findByText(/no companies saved yet/i);

    fireEvent.change(screen.getByLabelText(/company name/i), {
      target: { value: "Acme Games" },
    });
    expect(
      screen.queryByLabelText(/official website/i),
    ).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/^careers page$/i), {
      target: { value: "https://jobs.lever.co/acme" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Review company" }));

    const confirm = await screen.findByRole("button", {
      name: "Confirm and save",
    });
    expect(screen.queryByText(/legal employer/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/monitoring provider/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/\bLCA\b/i)).not.toBeInTheDocument();
    expect(requests.some((request) => request.path.endsWith("/save"))).toBe(
      false,
    );

    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /alert me about matching jobs already present/i,
      }),
    );
    fireEvent.click(confirm);

    await waitFor(() => {
      expect(
        requests.filter((request) => request.path.endsWith("/save")),
      ).toHaveLength(1);
    });
    expect(
      requests.find((request) => request.path.endsWith("/save"))?.body,
    ).toBe(JSON.stringify({ notify_current_jobs: true }));
    expect(
      await screen.findByRole("button", { name: "Pause" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "First-poll alerts: On" }),
    ).toBeInTheDocument();
  });
});
