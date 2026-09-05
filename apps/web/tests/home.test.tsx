import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import Home from "@/app/page";

vi.mock("@/components/service-status", () => ({
  ServiceStatus: () => <span>Local services ready</span>,
}));

describe("foundation landing page", () => {
  it("explains the local-first product boundary", () => {
    render(<Home />);

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Find the employers worth watching.",
    );
    expect(
      screen.getByText(/not legal or immigration advice/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /discover companies/i }),
    ).toHaveAttribute("href", "/discover");
    const primaryNavigation = screen.getByRole("navigation", {
      name: /primary navigation/i,
    });
    expect(primaryNavigation).toContainElement(
      screen.getByRole("link", { name: "Discover" }),
    );
    expect(
      screen.getByRole("link", { name: "Saved companies" }),
    ).toHaveAttribute("href", "/companies");
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute(
      "href",
      "/settings",
    );
  });
});
