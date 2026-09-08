export const API_BASE = process.env.FFL_API_URL ?? "http://127.0.0.1:8300";

// The API is loopback FastAPI; anything slower than this is a hung ESPN call, and a server
// component must not hold the page open waiting for it.
export const API_TIMEOUT_MS = 20_000;

export class ApiError extends Error {
  constructor(public status: number, public source: string, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

export type Player = {
  player_id: number; name: string; pos: string; team: string; slot: string; bye: boolean;
  injury: string; proj_espn: number; proj_sleeper: number | null; proj: number; spread: number | null;
  espn_only: boolean; actual: number; game_played: number; season_proj: number;
  season_proj_sleeper: number | null;
};
export type LineupEntry = { slot: string; player_id: number | null; name: string; proj: number };
export type Swap = { slot: string; out: string; in: string; gain: number };
export type Side = {
  team_id: number; team: string; lineup: Player[]; bench: Player[]; ir: Player[];
  optimal: LineupEntry[]; optimal_swaps: Swap[]; proj_total: number; optimal_total: number; actual_total: number;
};
export type Week = { week: number; live: boolean; me: Side; opp: Side | null; stale_since: string | null };
export type WaiverRow = Player & {
  ros_proj: number; week_proj: number; delta_roster: number; delta_pool: number;
  would_start: boolean; week_delta: number; add: number;
};
export type Waivers = { week: number; pos: string; rows: WaiverRow[]; stale_since: string | null };
export type League = {
  week: number;
  standings: { rank: number; team_id: number; team: string; wins: number; losses: number; ties: number; points_for: number; points_against: number; me: boolean }[];
  scoreboard: { home: string; away: string; home_score: number; away_score: number }[];
  power: { team: string; score: number }[];
  stale_since: string | null;
};
export type Journal = { decisions_md: string; transactions: Record<string, string>[]; stale_since: null };
export type Draft = { picks: Record<string, string>[]; recap_md: string; stale_since: null };

async function handle<T>(r: Response): Promise<T> {
  if (r.ok) return (await r.json()) as T;
  let detail: { source?: string; error?: string } = {};
  try { detail = (await r.json()).detail ?? {}; } catch {}
  throw new ApiError(r.status, detail.source ?? "api", detail.error ?? r.statusText);
}

// A failed fetch() rejects with a TypeError whose `cause` is an AggregateError of
// ECONNREFUSED errors. React Flight cannot serialise that chain (Next 16 crashes with
// `chunk.reason.enqueueModel is not a function`), so never let it escape: flatten it into
// a fresh ApiError with no `cause`. An AbortSignal.timeout abort arrives as a bare
// TimeoutError DOMException with no `cause`, so name it by its `name`.
export function unreachable(e: unknown): never {
  const cause = e instanceof Error ? (e.cause as { code?: string } | undefined) : undefined;
  const named = e instanceof Error && e.name && e.name !== "Error" ? e.name : undefined;
  const detail = cause?.code ?? named ?? (e instanceof Error ? e.message : String(e));
  throw new ApiError(0, "api", `cannot reach the API: ${detail}`);
}

export async function apiGet<T>(path: string): Promise<T> {
  let r: Response;
  try {
    r = await fetch(`${API_BASE}${path}`, { cache: "no-store", signal: AbortSignal.timeout(API_TIMEOUT_MS) });
  } catch (e) {
    unreachable(e);
  }
  return handle<T>(r);
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  let r: Response;
  try {
    r = await fetch(`${API_BASE}${path}`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
      cache: "no-store", signal: AbortSignal.timeout(API_TIMEOUT_MS),
    });
  } catch (e) {
    unreachable(e);
  }
  return handle<T>(r);
}
