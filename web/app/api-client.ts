"use client";

import { apiErrorMessage } from "@/lib/api-error";
import { signedOutRedirect } from "@/lib/auth";
import { leagueOf, withLeague } from "@/lib/league";

// Same ceiling as lib/api.ts, redeclared so the client bundle does not pull in the server module.
const API_TIMEOUT_MS = 20_000;

// A refused POST: the message is apiErrorMessage's readable line; `status` and `body` are kept for
// callers that branch on them (the claims submit: 409 running, 422 reasons, 502 login expired).
export class ClientError extends Error {
  constructor(public status: number, message: string, public body: unknown) {
    super(message);
  }
}

// The open league is the page's (?league= in its URL, lib/league.ts): every call names it, so a move, a claim or a
// refetch on another league's page never lands on home's. Global routes ignore the parameter.
const leagued = (path: string) => withLeague(path, typeof window === "undefined" ? null : leagueOf(window.location.search));

async function bodyOf(r: Response): Promise<unknown> {
  try {
    return await r.json();
  } catch {
    return null;
  }
}

// A call the API refused because the session is gone (expired mid-use, signed out elsewhere): a full page load to sign
// in, back to this page after. The promise never settles, so no caller shows an error toast while the page leaves.
function toSignIn(status: number, body: unknown): Promise<never> | null {
  const href = signedOutRedirect(status, body, window.location.pathname, window.location.search);
  if (!href) return null;
  window.location.assign(href);
  return new Promise<never>(() => {});
}

// Browser-side calls go through Next's /api rewrite, never to :8300 directly; the session cookie rides along (same origin).
export async function clientGet<T>(path: string): Promise<T> {
  const r = await fetch(`/api${leagued(path)}`, { cache: "no-store", signal: AbortSignal.timeout(API_TIMEOUT_MS) });
  if (!r.ok) {
    if (r.status === 401) {
      const away = toSignIn(r.status, await bodyOf(r));
      if (away) return away;
    }
    throw new Error(`${r.status} ${r.statusText}`);
  }
  return (await r.json()) as T;
}

export async function clientPost<T>(path: string, body: unknown = {}, timeoutMs = API_TIMEOUT_MS): Promise<T> {
  const r = await fetch(`/api${leagued(path)}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!r.ok) {
    // The decision form must show *why* the API said no (a 422 names the blank field), so
    // read the body before throwing rather than reporting a bare status line.
    const parsed = await bodyOf(r);
    const away = toSignIn(r.status, parsed);
    if (away) return away;
    throw new ClientError(r.status, apiErrorMessage(r.status, r.statusText, parsed), parsed);
  }
  return (await r.json()) as T;
}
