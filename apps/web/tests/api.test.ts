import { afterEach, describe, expect, it, vi } from "vitest";

import { apiRequest } from "@/lib/api";
import {
  getLocalDayWindow,
  LOCAL_DAY_END_HEADER,
  LOCAL_DAY_START_HEADER,
} from "@/lib/local-day";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("browser-local API context", () => {
  it("adds the current system day's exact midnight boundaries to every request", async () => {
    vi.useFakeTimers();
    const localNow = new Date(2026, 7, 29, 14, 35, 0);
    vi.setSystemTime(localNow);
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        void input;
        void init;
        return new Response(JSON.stringify({ ready: true }), {
          headers: { "Content-Type": "application/json" },
          status: 200,
        });
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    await apiRequest<{ ready: boolean }>("/health/ready");

    const expectedDay = getLocalDayWindow(localNow);
    const requestHeaders = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(requestHeaders.get(LOCAL_DAY_START_HEADER)).toBe(expectedDay.start);
    expect(requestHeaders.get(LOCAL_DAY_END_HEADER)).toBe(expectedDay.end);
  });
});
