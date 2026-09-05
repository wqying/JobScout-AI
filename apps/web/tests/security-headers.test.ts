import { describe, expect, it } from "vitest";

import nextConfig from "@/next.config";

describe("web security headers", () => {
  it("allows the inline bootstrap scripts required to hydrate static Next.js pages", async () => {
    const headerRules = await nextConfig.headers?.();
    const contentSecurityPolicy = headerRules
      ?.flatMap((rule) => rule.headers)
      .find((header) => header.key === "Content-Security-Policy");

    expect(contentSecurityPolicy?.value).toContain(
      "script-src 'self' 'unsafe-inline'",
    );
  });
});
