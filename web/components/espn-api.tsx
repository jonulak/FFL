"use client";

import { ClientError, clientPost } from "@/app/api-client";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { signedOutRedirect } from "@/lib/auth";
import { espnMessage, initials } from "@/lib/espn";

// The ESPN connect calls the wizard and Settings share, and how a refusal reads: the API's own sentence
// (`detail.message`, ffl/espn_connect.py) rather than clientPost's status line.

export function why(e: unknown, fallback = "Something went wrong. Try again."): string {
  return e instanceof ClientError ? espnMessage(e.body, fallback) : fallback;
}

// A GET that keeps the API's answer on a refusal (clientGet keeps only the status line).
export async function espnGet<T>(path: string): Promise<T> {
  const r = await fetch(`/api${path}`, { cache: "no-store", signal: AbortSignal.timeout(30_000) });
  let body: unknown = null;
  try {
    body = await r.json();
  } catch {
    body = null;
  }
  if (!r.ok) {
    // the session went (expired, signed out elsewhere): to sign in and back, as clientGet does; never settles
    const away = signedOutRedirect(r.status, body, window.location.pathname, window.location.search);
    if (away) {
      window.location.assign(away);
      return new Promise<never>(() => {});
    }
    throw new ClientError(r.status, espnMessage(body, `${r.status} ${r.statusText}`), body);
  }
  return body as T;
}

export const espnPost = clientPost;

// A league's team mark: ESPN's logo over the team's initials (a broken or missing logo leaves the initials).
export function TeamMark({ logo, team, dim = false }: { logo: string | null; team: string | null; dim?: boolean }) {
  return (
    <Avatar className={`size-10 rounded-lg after:rounded-lg ${dim ? "opacity-60" : ""}`}>
      {logo && <AvatarImage src={logo} alt="" className="rounded-lg object-cover" />}
      <AvatarFallback className="rounded-lg font-display text-base font-bold">{initials(team)}</AvatarFallback>
    </Avatar>
  );
}
