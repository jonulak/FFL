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
  season_proj_sleeper: number | null; return_week: number | null; injury_note: string | null;
  // rest of season: the mean of Sleeper's remaining weeks and CBS's ROS page, null when neither
  // has him (the API then scales season_proj); ros_spread is the gap when both do
  ros_proj: number | null; ros_sleeper: number | null; ros_cbs: number | null; ros_spread: number | null;
  opponent: string | null; kickoff: string | null; home: boolean | null;
  game?: GameState; // present once his pro team has kicked off (live overlay, ffl/live.py)
  stat_line?: string; // his box-score line, once a stat that scores has happened (live.stat_line)
  form?: Form | null; // his last three games (STRATEGY.md "Recent form"); Roster cards and Waiver rows
};
// season.recent_form: the last three games he played, facts beside `add`, never inside it.
// Null columns are the ones his position has no number for (snaps for a K, targets for a QB),
// or that no game in the window has yet (snaps land a day after the games: `snap_games` says
// how many of the `games` carry them).
export type Form = {
  games: number; weeks: number[];
  l3: number; beat: number; opp: number | null; opp_trend: number | null;
  snap_pct: number | null; snap_games: number; tgt_snap: number | null; ypt: number | null;
  pace_gap: number;
};
export type GameState = {
  status: "in" | "post"; quarter: number | null; clock: string | null;
  score: Record<string, number>; // team abbreviation -> points, both sides
};
export type Card = Player & {
  group: "starter" | "bench" | "ir"; call: "START" | "BENCH" | "OUT" | "BYE" | "IR";
  gap: number | null; close_call: boolean; reason: string;
  outlook: string | null; outlook_at: string | null; outlook_stale: boolean;
};
export type OutlookRun = {
  at: string | null; generated: number; skipped: number; failed: number; running: boolean;
  error: string | null; last_failure: string | null;
};
export type Roster = { week: number; live: boolean; cards: Card[]; outlook_run: OutlookRun; stale_since: string | null };
export type OutlookStarted = { started: boolean; pending: number };
export type LineupEntry = { slot: string; player_id: number | null; name: string; proj: number };
export type Swap = { slot: string; out: string; in: string; gain: number };
// `abbrev` and `logo` as on a Club, so the scorebug can carry the mark (logo "" when ESPN has none).
export type Side = {
  team_id: number; team: string; abbrev: string; logo: string; lineup: Player[]; bench: Player[]; ir: Player[];
  optimal: LineupEntry[]; optimal_swaps: Swap[]; proj_total: number; optimal_total: number; actual_total: number;
};
// One row of the play feed (ffl/plays.py): a starter's points moved by `delta` to `total`.
export type Play = {
  at: string; week: number; player_id: number; name: string; pos: string; team: string;
  side: "me" | "opp"; team_id: number; delta: number; total: number; text: string | null;
  play_id: string | null; quarter: number | null; clock: string | null; game_id: string | null;
  match?: "player" | "last"; // "player": a play naming him; "last": the game's last play, possibly the next one
};
export type Plays = { week: number | null; live: boolean; plays: Play[]; stale_since: null };
export type Week = { week: number; live: boolean; me: Side; opp: Side | null; plays: Play[]; stale_since: string | null };
export type WaiverRow = Player & {
  ros_proj: number; week_proj: number; delta_roster: number; delta_pool: number;
  would_start: boolean; week_delta: number; add: number;
};
// ros_source "season": no source had anyone, every ROS is a scaled draft-day season total
export type Waivers = { week: number; pos: string; rows: WaiverRow[]; ros_source: "sources" | "season"; stale_since: string | null };
// A fantasy team as the League page shows it; `logo` is the owner's ESPN upload ("" when none).
export type Club = { team_id: number; team: string; abbrev: string; logo: string; wins: number; losses: number; ties: number };
export type Standing = Club & { rank: number; points_for: number; points_against: number; streak: string; me: boolean };
// starter_ids is what the API re-sums a side from while the live feed runs; empty for a future week.
export type ScoreSide = Club & { score: number; proj: number; me: boolean; starter_ids: number[] };
export type League = {
  week: number; current_week: number; weeks: number; playoff_teams: number; live: boolean;
  standings: Standing[];
  scoreboard: { home: ScoreSide; away: ScoreSide }[];
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
