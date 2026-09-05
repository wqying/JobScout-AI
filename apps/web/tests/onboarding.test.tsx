import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProfileSettingsForm } from "@/components/forms/profile-settings-form";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    status,
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("local onboarding", () => {
  it("persists the profile before settings without asking for a timezone", async () => {
    const requests: Array<{
      body: Record<string, unknown> | null;
      method: string;
      path: string;
    }> = [];
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        const method = init?.method ?? "GET";
        requests.push({
          body: init?.body
            ? (JSON.parse(String(init.body)) as Record<string, unknown>)
            : null,
          method,
          path,
        });

        if (path.endsWith("/profile") && method === "GET") {
          return jsonResponse({
            display_name: "Ada Student",
            email: "ada@example.com",
            country_code: "US",
          });
        }
        if (path.endsWith("/settings") && method === "GET") {
          return jsonResponse({
            role_types: ["internship"],
            keywords: ["python"],
            excluded_keywords: [],
            preferred_locations: ["Pittsburgh"],
            remote_preference: "hybrid",
            notify_current_jobs_on_save: false,
            email_notifications_enabled: false,
            notification_email: null,
          });
        }
        if (method === "PUT") {
          return jsonResponse({});
        }
        throw new Error(`Unexpected request: ${method} ${path}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<ProfileSettingsForm mode="onboarding" />);

    expect(await screen.findByDisplayValue("Ada Student")).toBeInTheDocument();
    expect(
      screen.queryByRole("textbox", { name: /time.?zone/i }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Create local profile" }),
    );

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Local profile created",
    );
    await waitFor(() => {
      const writes = requests.filter((request) => request.method === "PUT");
      expect(writes.map(({ method, path }) => ({ method, path }))).toEqual([
        { method: "PUT", path: "/api/v1/profile" },
        { method: "PUT", path: "/api/v1/settings" },
      ]);
      expect(writes[1]?.body).not.toHaveProperty("timezone");
    });
  });
});
