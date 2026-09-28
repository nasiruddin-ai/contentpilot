"use client";

/** Thin client for the ContentPilot API. Cookies carry auth; a 401 triggers one refresh + retry. */

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: { field: string; message: string }[],
  ) {
    super(message);
  }
}

type Query = Record<string, string | number | boolean | undefined | null>;

function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `${path}?${text}` : path;
}

let refreshing: Promise<boolean> | null = null;

async function refreshSession(): Promise<boolean> {
  refreshing ??= fetch("/api/v1/auth/refresh", { method: "POST", credentials: "include" })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

async function parseError(response: Response): Promise<ApiError> {
  try {
    const body = await response.json();
    const error = body?.error ?? {};
    return new ApiError(response.status, error.code ?? "HTTP_ERROR", error.message ?? response.statusText, error.details);
  } catch {
    return new ApiError(response.status, "HTTP_ERROR", response.statusText);
  }
}

async function request<T>(method: string, path: string, body?: unknown, retry = true): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: "include",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 401 && retry && !path.startsWith("/api/v1/auth/")) {
    if (await refreshSession()) return request<T>(method, path, body, false);
    // Session is gone: a full navigation clears all client state. This runs outside React, so no router here.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    if (typeof window !== "undefined") window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
  }
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string, query?: Query) => request<T>("GET", withQuery(path, query)),
  post: <T>(path: string, body?: unknown, query?: Query) => request<T>("POST", withQuery(path, query), body ?? {}),
  patch: <T>(path: string, body: unknown, query?: Query) => request<T>("PATCH", withQuery(path, query), body),
  delete: <T = void>(path: string, query?: Query) => request<T>("DELETE", withQuery(path, query)),
};

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.details?.length) return error.details.map((d) => `${d.field}: ${d.message}`).join("; ");
    return error.message;
  }
  return error instanceof Error ? error.message : "Something went wrong.";
}
