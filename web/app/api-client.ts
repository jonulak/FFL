"use client";

import { apiErrorMessage } from "@/lib/api-error";

// Same ceiling as lib/api.ts, redeclared so the client bundle does not pull in the server module.
const API_TIMEOUT_MS = 20_000;

// Browser-side calls go through Next's /api rewrite, never to :8300 directly.
export async function clientGet<T>(path: string): Promise<T> {
  const r = await fetch(`/api${path}`, { cache: "no-store", signal: AbortSignal.timeout(API_TIMEOUT_MS) });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return (await r.json()) as T;
}

export async function clientPost<T>(path: string, body: unknown = {}, timeoutMs = API_TIMEOUT_MS): Promise<T> {
  const r = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!r.ok) {
    // The decision form must show *why* the API said no (a 422 names the blank field), so
    // read the body before throwing rather than reporting a bare status line.
    let parsed: unknown = null;
    try {
      parsed = await r.json();
    } catch {}
    throw new Error(apiErrorMessage(r.status, r.statusText, parsed));
  }
  return (await r.json()) as T;
}
