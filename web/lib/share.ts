// The share image's request and sources, pure so the route (app/share/route.tsx) can trust them.
export type ShareKind = "roster" | "matchup";
// `teams`: whether the image names the fantasy teams (name, logo, record).
export type ShareRequest = { kind: ShareKind; week: number; team: number | null; teams: boolean };

const positive = (raw: string | null): number | null => {
  const n = Number(raw);
  return raw && Number.isInteger(n) && n > 0 ? n : null;
};

// `?kind=&week=[&team=][&teams=0]` back as values; null when any is malformed. They go into an API path.
export function parseShare(q: URLSearchParams): ShareRequest | null {
  const kind = q.get("kind");
  const week = positive(q.get("week"));
  const raw = q.get("team");
  const team = positive(raw);
  const teams = q.get("teams");
  if ((kind !== "roster" && kind !== "matchup") || week == null || (raw != null && team == null) || (teams != null && teams !== "0")) return null;
  return { kind, week, team, teams: teams == null };
}

// Where the server may fetch a club's mark: its own API, or ESPN's image CDN. A league owner can set any URL as a
// logo, so anything else is the abbreviation tile; fetching it would let an owner aim this server at its own network.
export function markSource(src: string): { api: string } | { cdn: string } | null {
  if (src.startsWith("/api/logo/")) return { api: src.slice("/api".length) };
  try {
    const u = new URL(src);
    return u.protocol === "https:" && u.hostname.endsWith(".espncdn.com") ? { cdn: u.href } : null;
  } catch {
    return null;
  }
}

// Two #rrggbb colours mixed, `share` of the first: the hex satori needs for TeamPanel's color-mix gradient stop.
export function mixHex(a: string, b: string, share: number): string {
  const ch = (hex: string, i: number) => parseInt(hex.slice(i, i + 2), 16);
  return `#${[1, 3, 5].map((i) => Math.round(ch(a, i) * share + ch(b, i) * (1 - share)).toString(16).padStart(2, "0")).join("")}`;
}

export function shareHref({ kind, week, team, teams }: ShareRequest): string {
  return `/share?kind=${kind}&week=${week}${team ? `&team=${team}` : ""}${teams ? "" : "&teams=0"}`;
}

type Sharer = { canShare?: (d: ShareData) => boolean; share?: (d: ShareData) => Promise<void> };

// Hand the image to the share sheet. "blocked": the browser no longer counts the tap as fresh (the image took too
// long), so only a new tap can open the sheet. "unsupported": this browser cannot share a file, so save it instead.
export async function shareFile(file: File, nav: Sharer): Promise<"shared" | "cancelled" | "blocked" | "unsupported"> {
  if (!nav.share || !nav.canShare?.({ files: [file] })) return "unsupported";
  try {
    await nav.share({ files: [file] });
    return "shared";
  } catch (e) {
    const name = (e as Error).name;
    if (name === "AbortError") return "cancelled";
    if (name === "NotAllowedError") return "blocked";
    throw e;
  }
}
