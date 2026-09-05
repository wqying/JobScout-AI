import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DailyDiscoveryHistory } from "@/components/discovery/daily-history";
import {
  LOCAL_DAY_END_HEADER,
  LOCAL_DAY_START_HEADER,
} from "@/lib/local-day";

type HistoryItem = {
  id: string;
  query: string;
  status: "queued" | "running" | "succeeded" | "failed";
  cached: boolean;
  result_count: number;
  error_code: string | null;
  created_at: string;
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    status,
  });
}

function historyResponse(items: HistoryItem[]) {
  return jsonResponse({ items, next_cursor: null });
}

function item(overrides: Partial<HistoryItem> = {}): HistoryItem {
  return {
    id: "run-1",
    query: "gaming companies",
    status: "succeeded",
    cached: false,
    result_count: 2,
    error_code: null,
    created_at: "2026-08-29T14:00:00Z",
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Daily history", () => {
  it("shows a loading state while today's searches are requested", async () => {
    const fetchMock = vi.fn(() => new Promise<Response>(() => undefined));
    vi.stubGlobal("fetch", fetchMock);

    render(<DailyDiscoveryHistory />);

    expect(screen.getByRole("status")).toHaveTextContent(
      "Loading today's searches",
    );
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("links completed searches back to their persisted results", async () => {
    const fetchMock = vi.fn(async () => historyResponse([item()]));
    vi.stubGlobal("fetch", fetchMock);

    render(<DailyDiscoveryHistory />);

    const link = await screen.findByRole("link", {
      name: "Open gaming companies search (Ready)",
    });
    expect(link).toHaveAttribute("href", "/discover/run-1");
    expect(screen.getByText("2 results")).toBeInTheDocument();
  });

  it("appends earlier searches from the same local day", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ items: [item()], next_cursor: "run-older" }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          items: [
            item({ id: "run-older", query: "healthcare companies" }),
          ],
          next_cursor: null,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(<DailyDiscoveryHistory />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Load earlier today" }),
    );

    expect(await screen.findByText("healthcare companies")).toBeInTheDocument();
    expect(screen.getByText("gaming companies")).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[1]?.[0])).toBe(
      "/api/v1/discoveries/daily-history?limit=10&cursor=run-older",
    );
    expect(
      screen.queryByRole("button", { name: "Load earlier today" }),
    ).not.toBeInTheDocument();
  });

  it("explains when no industries have been searched today", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => historyResponse([])));

    render(<DailyDiscoveryHistory />);

    expect(
      await screen.findByText(/No industries searched today yet/i),
    ).toBeInTheDocument();
  });

  it("lets the user retry after a history request fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(
          { error: { code: "DATABASE_ERROR", message: "History unavailable." } },
          503,
        ),
      )
      .mockResolvedValueOnce(historyResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    render(<DailyDiscoveryHistory />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "History unavailable.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(
      await screen.findByText(/No industries searched today yet/i),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("polls an active search until its results are ready", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        historyResponse([item({ status: "running", result_count: 0 })]),
      )
      .mockResolvedValueOnce(historyResponse([item()]));
    vi.stubGlobal("fetch", fetchMock);

    render(<DailyDiscoveryHistory />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(screen.getByText("Researching")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });

    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("2 results")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("rolls history forward when focus returns on a new local day", async () => {
    vi.useFakeTimers();
    const firstDay = new Date(2026, 7, 29, 12, 0, 0);
    const secondDay = new Date(2026, 7, 30, 8, 0, 0);
    vi.setSystemTime(firstDay);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(historyResponse([item()]))
      .mockResolvedValueOnce(historyResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    render(<DailyDiscoveryHistory />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("gaming companies")).toBeInTheDocument();

    vi.setSystemTime(secondDay);
    await act(async () => {
      fireEvent.focus(window);
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(
      screen.getByText(/No industries searched today yet/i),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);

    const secondRequestHeaders = new Headers(
      fetchMock.mock.calls[1]?.[1]?.headers,
    );
    expect(secondRequestHeaders.get(LOCAL_DAY_START_HEADER)).toBe(
      new Date(2026, 7, 30).toISOString(),
    );
    expect(secondRequestHeaders.get(LOCAL_DAY_END_HEADER)).toBe(
      new Date(2026, 7, 31).toISOString(),
    );
  });
});
