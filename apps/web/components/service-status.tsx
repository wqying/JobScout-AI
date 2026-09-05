"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";

type Readiness = "checking" | "ready" | "unavailable";

export function ServiceStatus() {
  const [readiness, setReadiness] = useState<Readiness>("checking");

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/v1/health/ready", {
      cache: "no-store",
      signal: controller.signal,
    })
      .then((response) => setReadiness(response.ok ? "ready" : "unavailable"))
      .catch(() => {
        if (!controller.signal.aborted) setReadiness("unavailable");
      });
    return () => controller.abort();
  }, []);

  const label = {
    checking: "Checking local services…",
    ready: "Local services ready",
    unavailable: "Local services unavailable",
  }[readiness];

  return (
    <Badge
      aria-live="polite"
      className={
        readiness === "unavailable"
          ? "border-amber-400/30 bg-amber-400/10 text-amber-200"
          : undefined
      }
    >
      <span aria-hidden="true" className="mr-2">
        ●
      </span>
      {label}
    </Badge>
  );
}
