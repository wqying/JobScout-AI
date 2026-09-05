import {
  getLocalDayWindow,
  LOCAL_DAY_END_HEADER,
  LOCAL_DAY_START_HEADER,
} from "@/lib/local-day";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly code = "UNKNOWN_ERROR",
    public readonly status = 500,
  ) {
    super(message);
  }
}

export async function apiRequest<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const localDay = getLocalDayWindow();
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  headers.set(LOCAL_DAY_START_HEADER, localDay.start);
  headers.set(LOCAL_DAY_END_HEADER, localDay.end);

  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: { code?: string; message?: string };
    } | null;
    throw new ApiError(
      body?.error?.message ?? "The local JobScout API request failed.",
      body?.error?.code,
      response.status,
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
