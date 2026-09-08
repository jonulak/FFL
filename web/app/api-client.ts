"use client";

import { apiErrorMessage } from "@/lib/api-error";

// Browser-side calls go through Next's /api rewrite, never to :8300 directly.
export async function clientGet<T>(path: string): Promise<T> {
  const r = await fetch(`/api${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return (await r.json()) as T;
}

export async function clientPost<T>(path: string, body: unknown = {}): Promise<T> {
  const r = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
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
