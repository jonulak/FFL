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
  // STRATEGY.md "Usage-adjusted ROS": the two stories the blend cannot price, shown beside it —
  // years in the league (0 = rookie; null when Sleeper has no record) and the hurt teammates
  // at his position on ESPN's injury report
  exp: number | null; ahead: Ahead[];
  opponent: string | null; kickoff: string | null; home: boolean | null;
  game?: GameState; // present once his pro team has kicked off (live overlay, ffl/live.py)
  field?: Field; // his unit is on the field (live.field): the offense with the ball, a D/ST without it
  stat_line?: string; // his box-score line, once a stat that scores has happened (live.stat_line)
  box?: BoxItem[]; // the line items behind his points (live.box_score); Matchup payload only
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
  // what the window is worth to the usage-adjusted ROS (snap share against the position's lead
  // players, a game at most), and the games it is paced over
  weight: number; games_left: number;
};
// One scoring stat of his game: the count, the league's rate and the points it earned. A band
// row (points or yards allowed) is a flat award; its count is the total it was read off.
export type BoxItem = { stat_id: number; label: string; count: number; rate: number; flat: boolean; points: number };
// ffl/player_card.py: one player's game log, this season first. `summary` is the collapsed row's
// columns ([label, value], the position's own stat group); `box` the week's scoring breakdown.
// A bye or a missed game has no points and an empty box; `live` is the feed's week in progress.
// ESPN's rank of a defence against one position this season: 1 allowed the fewest points, `of` the
// most; `allowed` is the position's fantasy points per game against it.
export type OppRank = { rank: number; of: number; allowed: number };
export type WeekLog = {
  week: number; opp: string | null; home: boolean | null; status: "played" | "bye" | "out" | "live" | "upcoming";
  pts: number | null; summary: [string, string][]; box: BoxItem[];
  opp_rank?: OppRank | null; // this season's games, played or ahead
  // a game still to play: Sleeper's projection, averaged with ESPN's for the current week
  proj?: number | null; proj_espn?: number | null; proj_sleeper?: number | null;
};
export type SeasonTotals = { games: number; pts: number; ppg: number; summary: [string, string][]; box: BoxItem[] };
// `totals` is null for a season he has yet to finish a game in: the live feed opened it (his first
// game is in progress), or it holds only the weeks ahead.
export type SeasonLog = { season: number; weeks: WeekLog[]; totals: SeasonTotals | null };
export type PlayerLog = {
  player_id: number; name: string; pos: string; team: string; season: number; current_week: number; seasons: SeasonLog[];
  stale_since: string | null;
};
// A hurt teammate at his position: ESPN's status upper-cased, the return week when the feed has one.
export type Ahead = { name: string | null; status: string; return_week: number | null };
export type GameState = {
  status: "in" | "post"; quarter: number | null; clock: string | null;
  score: Record<string, number>; // team abbreviation -> points, both sides
  // while the game is on and someone has the ball (fastcast.possession): who, and inside the 20
  ball?: string; red_zone?: boolean;
};
export type Field = "on" | "red_zone";
export type OutlookSource = { site: string; date: string; url: string };
export type OutlookBullet = { label: "Injury" | "Usage" | "Depth" | "Team" | "Take"; text: string; sources: OutlookSource[] };
export type Card = Player & {
  group: "starter" | "bench" | "ir"; call: "START" | "BENCH" | "OUT" | "BYE" | "IR";
  gap: number | null; close_call: boolean; reason: string;
  outlook: OutlookBullet[] | null; outlook_at: string | null; outlook_stale: boolean;
  // a rewrite of this card alone is running (POST /outlook/{id}); the last one's failure, if it failed
  outlook_running: boolean; outlook_error: string | null;
};
// GET /outlook/{id}: one card's outlook fields, polled while its rewrite runs.
export type CardOutlook = Pick<Card, "outlook" | "outlook_at" | "outlook_stale" | "outlook_running" | "outlook_error">;
export type OutlookRun = {
  at: string | null; generated: number; skipped: number; failed: number; running: boolean;
  error: string | null; last_failure: string | null; pending: number | null;
};
export type Roster = { week: number; live: boolean; cards: Card[]; outlook_run: OutlookRun; stale_since: string | null };
export type OutlookStarted = { started: boolean; pending: number | null };
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
  match?: "player" | "defence" | "last"; // "player": a play naming him; "defence": a D/ST's play, read off its moved stats; "last": the game's last play, possibly the next one
};
// One game's whole week (GET /plays, `?team=` for any game but mine); `me`/`opp` as on its board.
// `live` keeps the page on the feed all game window; `game_on` is the badge: a starter in this game is playing.
export type PlaysSide = { team_id: number; team: string; abbrev: string };
export type Plays = {
  week: number | null; live: boolean; game_on: boolean; mine: boolean; me: PlaysSide | null; opp: PlaysSide | null;
  plays: Play[]; stale_since: string | null;
};
// `me` is the left side of the board, `opp` the right: I stand left of my own game (`mine`); any other
// game (a League scoreboard tile) reads away then home.
export type Week = { week: number; live: boolean; mine: boolean; me: Side; opp: Side | null; plays: Play[]; stale_since: string | null };
// ros_proj is the model mean (resolved: the season formula when no source has him); ros_adj the
// usage-adjusted number `add` and the deltas run on (STRATEGY.md "Usage-adjusted ROS").
export type WaiverRow = Player & {
  ros_proj: number; ros_adj: number; week_proj: number; delta_roster: number; delta_pool: number;
  would_start: boolean; week_delta: number; add: number;
  has_outlook: boolean;  // bullets written for him this week, from the Waivers dialog
};
// One free agent's outlook (GET /waivers/outlook/{id}): written on a click, a minute or two of
// Opus with web search, so `running` is polled. `error` is the last failed write's reason.
export type WaiverOutlook = {
  bullets: OutlookBullet[] | null; at: string | null; stale: boolean; running: boolean; error: string | null;
};
// One of my players as the Waivers table compares a free agent against him: the same ROS and
// week figures, by adjusted ROS best first (season.roster_rows).
export type RosterRow = Player & { ros_proj: number; ros_adj: number; week_proj: number };
// ros_source "season": no source had anyone, every ROS is a scaled draft-day season total.
// ros_week: the week ROS starts from; week_over: this week's games are all played (no week term).
export type Waivers = {
  week: number; ros_week: number; week_over: boolean; pos: string; rows: WaiverRow[]; roster: RosterRow[];
  ros_source: "sources" | "season"; stale_since: string | null;
};
// A fantasy team as the League page shows it; `logo` is the owner's ESPN upload ("" when none).
export type Club = { team_id: number; team: string; abbrev: string; logo: string; wins: number; losses: number; ties: number };
export type Standing = Club & { rank: number; points_for: number; points_against: number; streak: string; me: boolean };
// starter_ids is what the API re-sums a side from while the live feed runs, starter_teams what it marks
// a matchup live from (one of them in a game in progress); both empty for a future week.
export type ScoreSide = Club & { score: number; proj: number; me: boolean; starter_ids: number[]; starter_teams: string[] };
export type League = {
  week: number; current_week: number; weeks: number; playoff_teams: number; live: boolean;
  standings: Standing[];
  scoreboard: { home: ScoreSide; away: ScoreSide; live: boolean }[];
  stale_since: string | null;
};
// GET /team/{id}: any team's roster for the week (the team page), its club with the standings
// rank (null before ESPN has standings), `mine` when it is my own team.
export type TeamRoster = {
  week: number; live: boolean; mine: boolean; club: Club & { rank: number | null }; side: Side;
  stale_since: string | null;
};
export type Journal ={ decisions_md: string; transactions: Record<string, string>[]; stale_since: null };
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
