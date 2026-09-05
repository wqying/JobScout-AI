import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DiscoveryResults } from "@/components/discovery/discovery-results";
import { DiscoverySearch } from "@/components/discovery/discovery-search";

const navigation = vi.hoisted(() => ({
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ runId: "run-1" }),
  useRouter: () => ({ push: navigation.push }),
}));

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    status,
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  navigation.push.mockReset();
});

describe("company discovery", () => {
  it("starts an asynchronous industry search", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path.includes("/discoveries/daily-history?")) {
          return jsonResponse({ items: [], next_cursor: null });
        }
        expect(path).toBe("/api/v1/discoveries");
        expect(init?.method).toBe("POST");
        return jsonResponse(
          { id: "run-1", status: "queued", cached: false },
          202,
        );
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<DiscoverySearch />);

    fireEvent.change(screen.getByLabelText(/industry or company category/i), {
      target: { value: "gaming companies" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Research companies" }));

    await waitFor(() =>
      expect(navigation.push).toHaveBeenCalledWith("/discover/run-1"),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/discoveries",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("shows cached cost, sourced scores, and plain-language careers support", async () => {
    const result = {
      id: "result-1",
      company_id: "company-1",
      rank: 1,
      company_name: "Acme Games",
      careers_url: "https://jobs.lever.co/acme",
      opportunity_score: 65,
      scores: {
        industry: 90,
        historical_h1b_sponsorship: 25,
        internship: 100,
        careers_page_support: 100,
        current_openings: 0,
      },
      explanation: "Acme develops video games.",
      historical_h1b_status: "historical_records",
      certified_h1b_cases: 2,
      loaded_fiscal_years: [2025],
      internship_evidence: true,
      careers_url_status: "evidence_verified",
      careers_url_reason: "CAREERS_SOURCE_STRUCTURED_VERIFIED",
      monitoring_support: "structured",
      sources: [
        {
          source_id: "source_1",
          url: "https://acme.example/about",
          title: "About Acme",
          source_type: "official_company",
          supports_claims: ["industry"],
        },
      ],
      is_hidden: false,
      is_saved: false,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/discoveries/run-1")) {
        return jsonResponse({
          id: "run-1",
          query: "gaming companies",
          status: "succeeded",
          cached: true,
          result_count: 1,
          error_code: null,
          estimated_cost_usd: 0,
        });
      }
      if (path.includes("/discoveries/run-1/results?")) {
        return jsonResponse({
          items: [result],
          total: 1,
          offset: 0,
          limit: 20,
          has_more: false,
        });
      }
      throw new Error(`Unexpected request ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<DiscoveryResults />);

    expect(await screen.findByText("Acme Games")).toBeInTheDocument();
    expect(
      screen.getByText(/cache hit.*no new token use/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/after you save this company, JobScout can check/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/certified H1-B filings/i)).toBeInTheDocument();
    expect(screen.getByText("About Acme")).toBeInTheDocument();
    expect(screen.queryByText(/official website/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/work authorization/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/monitoring provider/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/legal employer/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/\bLCA\b/i)).not.toBeInTheDocument();
  });

  it("saves a discovered company and reloads its saved state", async () => {
    let saved = false;
    const result = {
      id: "result-1",
      company_id: "company-1",
      rank: 1,
      company_name: "Acme Games",
      careers_url: "https://jobs.lever.co/acme",
      opportunity_score: 65,
      scores: {
        industry: 90,
        historical_h1b_sponsorship: 25,
        internship: 100,
        careers_page_support: 100,
        current_openings: 0,
      },
      explanation: "Acme develops video games.",
      historical_h1b_status: "historical_records",
      certified_h1b_cases: 2,
      loaded_fiscal_years: [2025],
      internship_evidence: true,
      careers_url_status: "evidence_verified",
      careers_url_reason: "CAREERS_SOURCE_STRUCTURED_VERIFIED",
      monitoring_support: "structured",
      sources: [],
      is_hidden: false,
      is_saved: false,
    };
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path.endsWith("/discoveries/run-1/save-selected")) {
          expect(init?.method).toBe("POST");
          expect(JSON.parse(String(init?.body))).toEqual({
            result_ids: ["result-1"],
          });
          saved = true;
          return jsonResponse({ saved_company_ids: ["company-1"] });
        }
        if (path.endsWith("/discoveries/run-1")) {
          return jsonResponse({
            id: "run-1",
            query: "gaming companies",
            status: "succeeded",
            cached: false,
            result_count: 1,
            error_code: null,
            estimated_cost_usd: 0.01,
          });
        }
        if (path.includes("/discoveries/run-1/results?")) {
          return jsonResponse({
            items: [{ ...result, is_saved: saved }],
            total: 1,
            offset: 0,
            limit: 20,
            has_more: false,
          });
        }
        throw new Error(`Unexpected request ${path}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<DiscoveryResults />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Save company" }),
    );

    expect(await screen.findByRole("button", { name: "Saved" })).toBeDisabled();
    expect(saved).toBe(true);
  });

  it("loads every persisted result page without a show-more control", async () => {
    const baseResult = {
      id: "result-1",
      company_id: "company-1",
      rank: 1,
      company_name: "Acme Games",
      careers_url: null,
      opportunity_score: 50,
      scores: {
        industry: 80,
        historical_h1b_sponsorship: 50,
        internship: 50,
        careers_page_support: 0,
        current_openings: 0,
      },
      explanation: "Acme develops video games.",
      historical_h1b_status: "no_records",
      certified_h1b_cases: 0,
      loaded_fiscal_years: [],
      internship_evidence: false,
      careers_url_status: "not_found",
      careers_url_reason: "CAREERS_SOURCE_NOT_SELECTED",
      monitoring_support: "unsupported",
      sources: [],
      is_hidden: false,
      is_saved: false,
    };
    const secondResult = {
      ...baseResult,
      id: "result-2",
      company_id: "company-2",
      rank: 2,
      company_name: "Pixel Forge",
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/discoveries/run-1")) {
        return jsonResponse({
          id: "run-1",
          query: "gaming companies",
          status: "succeeded",
          cached: false,
          result_count: 2,
          error_code: null,
          estimated_cost_usd: 0.01,
        });
      }
      if (path.includes("offset=0&limit=20")) {
        return jsonResponse({
          items: [baseResult],
          total: 2,
          offset: 0,
          limit: 20,
          has_more: true,
        });
      }
      if (path.includes("offset=1&limit=20")) {
        return jsonResponse({
          items: [secondResult],
          total: 2,
          offset: 1,
          limit: 20,
          has_more: false,
        });
      }
      throw new Error(`Unexpected request ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<DiscoveryResults />);

    expect(await screen.findByText("Acme Games")).toBeInTheDocument();
    expect(await screen.findByText("Pixel Forge")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Show all search results" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Research more companies" }),
    ).toBeEnabled();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("research-more"),
      ),
    ).toBe(false);
  });

  it("shows an inline warning and starts research more without a dialog", async () => {
    let continuationFinished = false;
    const confirmMock = vi.spyOn(window, "confirm");
    const result = {
      id: "result-1",
      company_id: "company-1",
      rank: 1,
      company_name: "Acme Games",
      careers_url: null,
      opportunity_score: 50,
      scores: {
        industry: 80,
        historical_h1b_sponsorship: 50,
        internship: 50,
        careers_page_support: 0,
        current_openings: 0,
      },
      explanation: "Acme develops video games.",
      historical_h1b_status: "no_records",
      certified_h1b_cases: 0,
      loaded_fiscal_years: [],
      internship_evidence: false,
      careers_url_status: "not_found",
      careers_url_reason: "CAREERS_SOURCE_NOT_SELECTED",
      monitoring_support: "unsupported",
      sources: [],
      is_hidden: false,
      is_saved: false,
    };
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path.endsWith("/discoveries/run-1/research-more")) {
          expect(JSON.parse(String(init?.body))).toEqual({
            acknowledge_additional_api_usage: true,
          });
          return jsonResponse(
            {
              id: "continuation-1",
              query: "gaming companies",
              status: "queued",
              cached: false,
              result_count: 0,
              error_code: null,
              estimated_cost_usd: 0,
            },
            202,
          );
        }
        if (path.endsWith("/discoveries/continuation-1")) {
          continuationFinished = true;
          return jsonResponse({
            id: "continuation-1",
            query: "gaming companies",
            status: "succeeded",
            cached: false,
            result_count: 1,
            error_code: null,
            estimated_cost_usd: 0.01,
          });
        }
        if (path.endsWith("/discoveries/run-1")) {
          return jsonResponse({
            id: "run-1",
            query: "gaming companies",
            status: "succeeded",
            cached: false,
            result_count: 1,
            error_code: null,
            estimated_cost_usd: 0.01,
          });
        }
        if (path.includes("/discoveries/run-1/results?")) {
          return jsonResponse({
            items: continuationFinished
              ? [
                  result,
                  {
                    ...result,
                    id: "result-2",
                    company_id: "company-2",
                    rank: 2,
                    company_name: "Pixel Forge",
                  },
                ]
              : [result],
            total: continuationFinished ? 2 : 1,
            offset: 0,
            limit: 20,
            has_more: false,
          });
        }
        throw new Error(`Unexpected request ${path}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<DiscoveryResults />);
    expect(
      await screen.findByText(
        /additional token and web-search charges may apply/i,
      ),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Research more companies" }),
    );

    expect(confirmMock).not.toHaveBeenCalled();
    expect(
      await screen.findByText(/found 1 additional verified company/i),
    ).toBeInTheDocument();
  });

  it("can restore a hidden result without rerunning research", async () => {
    let hidden = false;
    const result = {
      id: "result-1",
      company_id: "company-1",
      rank: 1,
      company_name: "Acme Games",
      careers_url: "https://jobs.lever.co/acme",
      opportunity_score: 65,
      scores: {
        industry: 90,
        historical_h1b_sponsorship: 25,
        internship: 100,
        careers_page_support: 100,
        current_openings: 0,
      },
      explanation: "Acme develops video games.",
      historical_h1b_status: "historical_records",
      certified_h1b_cases: 2,
      loaded_fiscal_years: [2025],
      internship_evidence: true,
      careers_url_status: "evidence_verified",
      careers_url_reason: "CAREERS_SOURCE_STRUCTURED_VERIFIED",
      monitoring_support: "structured",
      sources: [],
      is_hidden: false,
      is_saved: false,
    };
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path.endsWith("/discoveries/run-1/results/result-1/hide")) {
          expect(init?.method).toBe("POST");
          hidden = true;
          return new Response(null, { status: 204 });
        }
        if (path.endsWith("/discoveries/run-1/results/result-1/show")) {
          expect(init?.method).toBe("POST");
          hidden = false;
          return new Response(null, { status: 204 });
        }
        if (path.endsWith("/discoveries/run-1")) {
          return jsonResponse({
            id: "run-1",
            query: "gaming companies",
            status: "succeeded",
            cached: false,
            result_count: 1,
            error_code: null,
            estimated_cost_usd: 0.01,
          });
        }
        if (path.includes("/discoveries/run-1/results?")) {
          return jsonResponse({
            items: [{ ...result, is_hidden: hidden }],
            total: 1,
            offset: 0,
            limit: 20,
            has_more: false,
          });
        }
        throw new Error(`Unexpected request ${path}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<DiscoveryResults />);
    fireEvent.click(await screen.findByRole("button", { name: "Hide result" }));

    expect(
      await screen.findByText(
        "Acme Games is hidden. Use Show result to restore it.",
      ),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "Show result" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Save company" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show result" }));

    expect(
      await screen.findByText("Acme Games is visible again."),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "Hide result" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save company" }),
    ).toBeInTheDocument();
  });
});
