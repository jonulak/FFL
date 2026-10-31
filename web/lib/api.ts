import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import { PATH_HEADER, sessionCookie, signedOutRedirect } from "./auth";
import { LEAGUE_HEADER, leagueMissRedirect, withLeague } from "./league";

export const API_BASE = process.env.FFL_API_URL ?? "http://127.0.0.1:8300";

// GET /leagues (ffl-soc.4.13): the switcher's rows, home first; `format` null when its settings could not be read.
export type LeagueEntry = { key: string; name: string; provider: string; format: string | null; home: boolean };

// The API is loopback FastAPI; anything slower than this is a hung ESPN call, and a server
// component must not hold the page open waiting for it.
export const API_TIMEOUT_MS = 20_000;

// ffl/notify.py: the Settings page's switches and quiet window, and the outbox rows it lists
export type NotifyPrefs = { move: boolean; injury: boolean; waivers: boolean; news: boolean; watch: boolean; espn: boolean; quiet: [string, string] };
// GET /llm/usage: what `claude -p` cost, one row per (UTC day, kind) over the last `days` days.
export type LlmUsage = { days: number; rows: { day: string; kind: string; calls: number; input: number; output: number; cost_usd: number }[] };
export type NotifyConfig = { public_key: string | null; enabled: boolean; prefs: NotifyPrefs; devices: number };

// ffl/auth.py GET /account: Settings' Account section (ffl-soc.6.2.4)
export type AccountSession = { id: string; created_at: string; last_seen: string; ua: string; current: boolean };
export type Account = {
  email: string; name: string | null; sessions: AccountSession[];
  identities: { provider: string; email: string; created_at: string }[]; linkable: string[];
};

export class ApiError extends Error {
  constructor(public status: number, public source: string, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

export type Player = {
  player_id: number; name: string; pos: string; team: string; slot: string; bye: boolean;
  // this week: one projection, the consensus of every source that has him (ESPN, Sleeper, CBS,
  // FantasySharks, FleaFlicker); spread is how far apart they are, n_src how many there were,
  // proj_src each one's number (highest first; the projection dialog)
  injury: string; proj: number; spread: number | null; n_src: number; proj_src: Record<string, number>;
  actual: number; game_played: number; season_proj: number;
  return_week: number | null; injury_note: string | null;
  // rest of season: the consensus of Sleeper's remaining weeks, CBS's and FantasySharks' ROS pages,
  // null when none has him (the API then scales season_proj); ros_n counts the sources
  ros_proj: number | null; ros_spread: number | null; ros_n: number | null;
  // the league's strategy, "Usage-adjusted ROS" (`uv run python -m ffl.strategy show`): the two stories the blend cannot price, shown beside it —
  // years in the league (0 = rookie; null when Sleeper has no record) and the hurt teammates
  // at his position on ESPN's injury report
  exp: number | null; ahead: Ahead[];
  opponent: string | null; kickoff: string | null; home: boolean | null;
  game?: GameState; // present once his pro team has kicked off (live overlay, ffl/live.py)
  field?: Field; // his unit is on the field (live.field): the offense with the ball, a D/ST without it
  stat_line?: string; // his box-score line, once a stat that scores has happened (live.stat_line)
  box?: BoxItem[]; // the line items behind his points (live.box_score); Matchup payload only
  volume?: Volume; // his targets / carries / pass attempts this week, beside the box (live.volume)
  form?: Form | null; // his last three games (the league's strategy, "Recent form" (`uv run python -m ffl.strategy show`)); Roster cards and Waiver rows
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
  // completed air yards (Sleeper has no intended ones) and red-zone looks over the `air_games` its
  // rows have landed for: a receiver's share of his club's (%), air yards per catch (per completion
  // for a QB), red-zone targets / rushes / pass attempts per game; null where the position has none
  air_share: number | null; air_catch: number | null; rz_tgt: number | null; rz_att: number | null;
  rz_pass: number | null; air_games: number;
};
// One scoring stat of his game: the count, the league's rate and the points it earned. A band
// row (points or yards allowed) is a flat award; its count is the total it was read off.
export type BoxItem = { stat_id: number; label: string; count: number; rate: number; flat: boolean; points: number };
// live.volume: the usage behind a game's points, which scores nothing itself — a WR's or TE's
// targets, an RB's carries, a QB's pass attempts. None for a K or a D/ST.
export type Volume = { count: number; label: string };
// ffl/player_card.py: one player's game log, this season first. `summary` is the collapsed row's
// columns ([label, value], the position's own stat group); `box` the week's scoring breakdown.
// A bye or a missed game has no points and an empty box; `live` is the feed's week in progress.
// ESPN's rank of a defence against one position this season: 1 allowed the fewest points, `of` the
// most; `allowed` is the position's fantasy points per game against it.
export type OppRank = { rank: number; of: number; allowed: number };
export type WeekLog = {
  week: number; opp: string | null; home: boolean | null; status: "played" | "bye" | "out" | "live" | "upcoming";
  pts: number | null; summary: [string, string][]; box: BoxItem[]; volume: Volume | null;
  opp_rank?: OppRank | null; // this season's games, played or ahead
  pos_rank?: number | null; // his rank at his position that week: this season's weeks before the current one
  // his share of the offensive snaps (Sleeper's, a day behind: null until they land); this season's
  // games played only, and never on a K or a D/ST
  snap_pct?: number | null;
  // a game still to play: the consensus this week, Sleeper's after
  proj?: number | null;
};
export type SeasonTotals = {
  games: number; pts: number; ppg: number; summary: [string, string][]; box: BoxItem[];
  pos_rank?: number | null; // this season's only: his rank at his position by points to date
  snap_pct?: number | null; snap_games?: number; // this season's only: his snap share over the games that have snaps
};
// `totals` is null for a season he has yet to finish a game in: the live feed opened it (his first
// game is in progress), or it holds only the weeks ahead.
export type SeasonLog = { season: number; weeks: WeekLog[]; totals: SeasonTotals | null };
export type PlayerLog = {
  player_id: number; name: string; pos: string; team: string; season: number; current_week: number; seasons: SeasonLog[];
  playoff_weeks?: number[]; // this season's fantasy playoff weeks, flagged on its rows
  jersey?: string | null; // ESPN's number as written ("41"); none for a D/ST
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
  group: "starter" | "bench" | "ir"; call: "START" | "BENCH" | "OUT" | "BYE" | "IR" | "SIT";
  // sat by hand for the week (POST /roster/sit): out of the call, SIT unless a bye or injury says more; model_call
  // is the call he would have had
  sat: boolean; model_call: Card["call"];
  gap: number | null; close_call: boolean; reason: string;
  // ESPN's lineupLocked, the playable projection the swap gain runs on, and the usage-adjusted rest of season
  play_proj: number; locked: boolean; ros_adj: number;
  // his place in ESPN's roster entries: a slot's starters stand in this order (season._card_sort_key)
  espn_order: number;
  // the slot a START puts him in (the league's strategy (`uv run python -m ffl.strategy show`), Slot by kickoff): the chip names it; null on any other call
  call_slot: string | null;
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
// position_caps is absent on a page payload cached before it was added
export type Roster = { week: number; live: boolean; cards: Card[]; empty_slots: string[]; position_caps?: PositionCap[]; writes: boolean;
  outlook_run: OutlookRun; stale_since: string | null; decision?: DecisionRef | null };
export type OutlookStarted = { started: boolean; pending: number | null };
export type LineupEntry = { slot: string; player_id: number | null; name: string; proj: number };
export type Swap = { slot: string; out: string; in: string; gain: number };
// `abbrev` and `logo` as on a Club, so the scorebug can carry the mark (logo "" when ESPN has none).
export type Side = {
  team_id: number; team: string; owner: string; wins: number; losses: number; ties: number; abbrev: string; logo: string; lineup: Player[]; bench: Player[]; ir: Player[];
  optimal: LineupEntry[]; optimal_swaps: Swap[]; proj_total: number; optimal_total: number; actual_total: number;
};
// One row of the play feed (ffl/plays.py): a starter's points moved by `delta` to `total`.
export type Play = {
  at: string; week: number; player_id: number; name: string; pos: string; team: string;
  side: "me" | "opp"; team_id: number; delta: number; total: number | null; text: string | null;
  play_id: string | null; quarter: number | null; clock: string | null; game_id: string | null;
  // "called_back" (ffl/playbook.py): a play called back that cost him points, never a points change (delta 0,
  // total null): `would_be` what it would have scored him, `kept` what it scored anyway (yards to a foul's spot)
  would_be?: number; kept?: number;
  // a row of his on a play since called back (ffl/plays.py fold_called_back): the call-back folded into it
  called_back?: { would_be: number; kept: number };
  match?: "player" | "defence" | "last" | "correction" | "reversed" | "adjusted" | "called_back"; // "reversed": points taken back in the game (a penalty, a review), text the play called back or flagged, or null; "adjusted": only yards moved on a play already scored (the ball re-spotted), text that play; "correction": a stat correction after the week, text says old → new, no quarter/clock; "player": a play naming him; "defence": a D/ST's play, read off its moved stats; "last": the game's last play, possibly the next one
};
// One game's whole week (GET /plays, `?team=` for any game but mine); `me`/`opp` as on its board.
// `live` keeps the page on the feed all game window; `game_on` is the badge: a starter in this game is playing.
export type PlaysSide = { team_id: number; team: string; abbrev: string };
export type Plays = {
  week: number | null; current: boolean; live: boolean; game_on: boolean; mine: boolean; me: PlaysSide | null; opp: PlaysSide | null;
  plays: Play[]; stale_since: string | null;
};
// The NFL tab (GET /nfl, GET /nfl/{week}/{team}): ESPN's public scoreboard and drives, with this week's two
// lineups marked on the plays and counted on each game (`mine`/`opp`).
export type NflClub = { abbr: string; id: string; name: string | null; location: string | null; record: string | null; score: number | null; winner: boolean };
export type NflGame = {
  event_id: string; kickoff: string; status: "pre" | "in" | "post"; detail: string | null; quarter: number | null; clock: string | null;
  network: string | null; away: NflClub; home: NflClub; ball: string | null; red_zone: boolean; down: string | null; mine: number; opp: number;
};
// `final`: every game in it is over; those sections come last, in time order
export type NflSection = { live: boolean; final?: boolean; kickoff: string | null; games: NflGame[] };
export type NflBoard = { week: number; current: number; weeks: number; playoff_weeks?: number[]; live: boolean; sections: NflSection[]; byes: string[]; stale_since: string | null };
export type NflPlay = {
  id: string; quarter: number | null; clock: string | null; team: string | null; down: string | null; text: string; kind: string | null;
  scoring: boolean; turnover: boolean; penalty: boolean; score: { away: number | null; home: number | null }; athletes: number[]; mine: string[]; opp: string[];
};
export type NflDrive = { id: string; team: string | null; description: string | null; result: string | null; plays: NflPlay[] };
// the Matchup board's players, whole: a tap opens the same breakdown (BoxScoreDialog)
export type NflStarter = Player;
// The two fantasy teams of my game heading the In this game table (mark and record as the scorebug has them);
// `teams` is null when the board failed.
export type NflTeam = { team_id: number; team: string; owner: string | null; abbrev: string; logo: string; wins: number; losses: number; ties: number };
export type NflGamePage = { week: number; live: boolean; game: NflGame; marked: boolean; starters: { me: NflStarter[]; opp: NflStarter[] }; teams?: { me: NflTeam | null; opp: NflTeam | null } | null; scoring: NflPlay[]; drives: NflDrive[]; stale_since: string | null };
// `me` is the left side of the board, `opp` the right: I stand left of my own game (`mine`); any other
// game (a League scoreboard tile) reads away then home.
// win_prob is the left side's odds (and the right's) off season.win_prob, re-priced by the live
// overlay; null with no opponent, and absent from a payload cached before it existed.
export type WinProb = { me: number; opp: number; sigma_source: "calibrated" | "fallback" };
export type Week = { week: number; live: boolean; mine: boolean; slots?: string[]; me: Side; opp: Side | null; win_prob?: WinProb | null; plays: Play[]; stale_since: string | null };
// ros_proj is the model mean (resolved: the season formula when no source has him); ros_adj the
// usage-adjusted number `add` and the deltas run on (the league's strategy, "Usage-adjusted ROS" (`uv run python -m ffl.strategy show`)).
// add and the deltas are "worth to me": on another team's player, what he'd add if I traded for
// him; null on my own (measuring him against my weakest is circular). owner null = free agent.
export type Owner = { team_id: number; name: string | null; abbrev: string | null };
export type PlayerRow = Player & {
  ros_proj: number; ros_adj: number; week_proj: number; delta_roster: number | null; delta_pool: number | null;
  would_start: boolean | null; week_delta: number | null; add: number | null;
  has_outlook: boolean;  // bullets written for him this week, from the row's outlook dialog (free agents only)
  // ESPN's waiver status: FREEAGENT is added instantly, WAIVERS is a claim; null when the read failed
  // (counts as WAIVERS) and on a rostered player. waiver_date: ms epoch his waiver period ends.
  status: "FREEAGENT" | "WAIVERS" | null; waiver_date: number | null;
  owner: Owner | null; mine: boolean;
};
// A free agent: the only row a claim, the START gain and the outlook dialog apply to.
export type FreeAgentRow = PlayerRow & {
  owner: null; mine: false; add: number; delta_roster: number; delta_pool: number; would_start: boolean; week_delta: number;
};
// One free agent's outlook (GET /waivers/outlook/{id}): written on a click, a minute or two of
// Opus with web search, so `running` is polled. `error` is the last failed write's reason.
export type WaiverOutlook = {
  bullets: OutlookBullet[] | null; at: string | null; stale: boolean; running: boolean; error: string | null;
};
// One of my players as the Waivers table compares a free agent against him: the same ROS and
// week figures, by adjusted ROS best first (season.roster_rows).
// droppable: false is ESPN's undroppable flag; locked: his game has started (no cut, for an instant add).
export type RosterRow = Player & { ros_proj: number; ros_adj: number; week_proj: number; droppable: boolean; locked: boolean };
// ros_source "season": no source had anyone, every ROS is a scaled draft-day season total.
// ros_week: the week ROS starts from; week_over: this week's games are all played (no week term).
// fa: the Free agents toggle as the API applied it (a search ignores it and spans everyone).
// capped: ALL or FLEX with no filter, cut to the first 150 by the view's sort.
export type Players = {
  week: number; ros_week: number; week_over: boolean; pos: string | null; q: string | null; fa: boolean;
  rows: PlayerRow[]; roster: RosterRow[];
  ros_source: "sources" | "season"; stale_since: string | null;
  view: ResolvedView; capped: boolean; presets: Preset[]; default: string;
};
// ffl/player_views.py: a view of the Players table. A numeric filter keeps the rows whose figure is at
// least (ge) or at most (le) the value; a set filter the rows in one of its buckets. Percent columns
// (snap_pct, air_share) are whole percents.
export type ViewFilter =
  | { field: string; op: "ge" | "le"; value: number }
  | { field: "injury" | "avail" | "team"; op: "in"; value: string[] };
export type ViewSort = { id: string; desc: boolean };
export type PlayerView = { pos: string; fa: boolean; cols: string[]; filters: ViewFilter[]; sort: ViewSort };
export type Preset = PlayerView & { id: string; name: string };
// the view /players applied: its preset with the URL's overrides laid over
export type ResolvedView = PlayerView & { preset: string; name: string };
export type PresetStore = { presets: Preset[]; default: string };
// GET/POST /claims: the week's "get K of N" claim groups (the claim_plans table). A group gets
// as many of its targets (best first) as it has rows under "make room": its cuts, first to go first,
// plus `open` empty roster spots (only one group may use open spots, and it uses all of them).
// A Claim is one move (the Python "Move"): an instant free-agent add (`now`) or a waiver claim.
export type ClaimGroup = { targets: number[]; cuts: number[]; open: number };
export type Claim = { group: number; add: number; drop: number | null };
// group null: a roster error, not a group's (an IR player with no injury designation blocks every add and claim).
export type ClaimErr = { group: number | null; player_id: number | null; reason: string };
// ESPN's pending claims; from_app marks the ones the last submit from here sent.
export type PendingClaim = { id: string; add: number; drop: number | null; priority: number | null; from_app: boolean };
// match: ESPN held what was sent, in order, at the read-back (absent on records from before it was kept).
export type ClaimSubmit = { at: string; now: Claim[]; claims: Claim[]; ids: string[]; match?: boolean };
export type PoolStatus = "FREEAGENT" | "WAIVERS";
// Every id in the groups plus every roster player; droppable/locked are null for a free agent.
export type ClaimPlayer = {
  name: string; pos: string; team: string; add: number | null; ros_adj: number | null; slot: string | null;
  status: PoolStatus | null; waiver_date: number | null; droppable: boolean | null; locked: boolean | null;
} & Partial<Pick<Player, "opponent" | "home" | "kickoff" | "bye" | "game" | "injury" | "injury_note" | "return_week">>; // the dialog header
// ESPN's cap on one position (settings position_limits) and how many I hold, IR included.
export type PositionCap = { pos: string; n: number; limit: number };
// warnings: a group whose wins could pass a position cap (ESPN refuses the claim that would); they do not block a send.
export type Claims = {
  week: number; groups: ClaimGroup[]; now: Claim[]; claims: Claim[]; errors: ClaimErr[]; warnings: ClaimErr[]; open_spots: number;
  position_caps: PositionCap[];
  status: Record<string, PoolStatus>; pending: PendingClaim[]; pending_error: string | null; last_submit: ClaimSubmit | null;
  // on_espn: ESPN's pending list, less the claims filed there directly, is this plan's claims in order (false unsent)
  edited_since_submit: boolean; on_espn: boolean;
  // writes false (FFL_WRITES=0, a test server): a submit goes to an in-memory fake, nothing reaches ESPN
  writes: boolean;
  players: Record<string, ClaimPlayer>;
};
// POST /claims/submit. readback_failed: ESPN's list could not be read after sending, so `pending`
// is empty for that reason, not because ESPN holds nothing.
export type SubmitResult = {
  now: Claim[]; now_failed: (Claim & { error: string })[]; sent: (Claim & { id: string })[];
  pending: { id: string; add: number; drop: number | null; priority?: number | null }[]; match: boolean; error: string | null; readback_failed: boolean; decisions?: DecisionRef[];
};
// A fantasy team as the League page shows it; `owner` is its manager's real name ("" when ESPN has
// none; co-managers joined with " & "), `logo` the owner's ESPN upload ("" when none).
export type Club = { team_id: number; team: string; owner: string; abbrev: string; logo: string; wins: number; losses: number; ties: number };
export type Standing = Club & { rank: number; points_for: number; points_against: number; streak: string; me: boolean };
// starter_ids is what the API re-sums a side from while the live feed runs, starter_teams what it marks
// a matchup live from (one of them in a game in progress), pending_teams the games ESPN does not yet call
// played (the API sets `final` once none is left, sooner on the feed's whistle); all empty for a future week.
// `starters` are those players as the board merges them (ESPN+Sleeper): `proj` is their sum, and the
// overlay re-prices the matchup's `win_prob` (null for a future week) off them as games run.
// `kickoff` is null in a payload cached before it was added; `game` is the live overlay's, once his game is on.
export type ScoreStarter = { player_id: number; pos: string; team: string; proj: number; actual: number; game_played: number; bye: boolean; injury: string; kickoff: string | null; game?: GameState };
export type ScoreSide = Club & { score: number; proj: number; me: boolean; starter_ids: number[]; starter_teams: string[]; pending_teams: string[]; starters: ScoreStarter[] };
export type League = {
  week: number; current_week: number; weeks: number; playoff_teams: number; playoff_weeks?: number[]; live: boolean;
  standings: Standing[];
  scoreboard: { home: ScoreSide; away: ScoreSide; live: boolean; final: boolean; win_prob?: { away: number; home: number } | null }[];
  stale_since: string | null;
};
// GET /team/{id}: any team's roster for the week (the team page), its club with the standings
// rank (null before ESPN has standings), `mine` when it is my own team.
export type TeamRoster = {
  week: number; live: boolean; mine: boolean; club: Club & { rank: number | null }; side: Side;
  stale_since: string | null;
};
// GET /transactions: the league's log as moves, newest first (season.group_transactions). A
// side's `added` is what it claimed or received, `dropped` what it cut; pos/team are null when
// ESPN's lookup failed or he is out of the NFL. Week 0 is before the season's first week.
export type TxPlayer = { player_id: number; name: string; pos: string | null; team: string | null };
export type TxSide = { team_id: number; team: string; abbrev: string; logo: string; added: TxPlayer[]; dropped: TxPlayer[] };
export type TxMove = { id: string; ms: number; at: string; kind: "waiver" | "free_agent" | "drop" | "trade"; week: number; sides: TxSide[] };
export type Transactions = { me: number; current_week: number; moves: TxMove[]; teams: Club[]; stale_since: string | null };
// GET /player/{id}/transactions: his moves read from the side he was on (season.player_transactions),
// newest first, then his draft pick (season.draft_entry) with round, pick in the round and overall.
// `at` is null on a pick read from ESPN's draft (the backup when the draft_picks table is empty).
export type PlayerTx = {
  id: string; at: string | null; week: number; kind: TxMove["kind"] | "dropped" | "traded" | "draft";
  team_id: number; team: string; abbrev: string; logo: string;
  with: string[]; from: { team_id: number; team: string } | null;
  round?: number; pick?: number; overall?: number;
};

// The news desk's feed (GET /news, ffl/newsdesk/feed.py). A row is one player a story moves: Jev's effect and kind,
// Haiku's line (`reason`, null until written) and `flag` when Haiku doubts the arrow; `owner` is who holds him now
// (read at request time, null for a free agent and when the owners read failed). Rows come ▲/▼ first, then ●.
export type NewsEffect = "up" | "down" | "none";
export type NewsRow = {
  player_id: number; name: string; pos: string | null; team: string | null;
  effect: NewsEffect; kind: string; reason: string | null; flag: boolean;
  owner: Owner | null; mine: boolean;
};
export type NewsCite = { source: string; url: string | null; at: string };
// `first` is the earliest live source (the scoop), `latest` the newest, `sources` all but the first by time; `at` the
// news time the feed is ordered by (the newest live source). `updated`: the story had a real update (rev > 1). A
// corrected story's status_note names the source that corrected it.
export type Story = {
  id: number; headline: string; overview: string; status: "live" | "corrected" | "retracted"; status_note: string | null;
  updated: boolean; at: string; first: NewsCite | null; latest: NewsCite | null; sources: NewsCite[]; players: NewsRow[];
};
// next_before: the cursor for Older, null at the end. owners_error / index_error: the read that failed, the feed
// served without owner tags / names.
export type NewsPage = { stories: Story[]; next_before: string | null; owners_error?: string; index_error?: string };
export type NewsPlayer = { player_id: number; name: string; pos: string | null; team: string | null; owner: Owner | null; mine: boolean };
export type PlayerNews = NewsPage & { player: NewsPlayer; muted: boolean };
export type NewsHit = { player_id: number; name: string; pos: string | null; team: string | null };

async function handle<T>(r: Response): Promise<T> {
  if (r.ok) return (await r.json()) as T;
  let body: unknown = null;
  try {
    body = await r.json();
  } catch {}
  // signed out (or the session expired): to sign in, back to this page. redirect() throws; errorFields rethrows it
  // past the pages' catch -> ErrorCard (lib/errors.ts)
  if (r.status === 401) {
    const [path, query = ""] = (await pagePath()).split("?", 2);
    const href = signedOutRedirect(r.status, body, path, query);
    if (href) redirect(href);
  }
  // a league the user is not in (a stale last-league cookie): the same page in their default; none at all: welcome
  if (r.status === 404) {
    const [path, query = ""] = (await pagePath()).split("?", 2);
    const href = leagueMissRedirect(r.status, body, path, query);
    if (href) redirect(href);
  }
  let detail: { source?: string; error?: string } = {};
  try {
    const d = (body as { detail?: unknown } | null)?.detail;
    // FastAPI's own HTTPException(404, "...") carries a plain string; the source-tagged 502s an object.
    detail = typeof d === "string" ? { error: d } : (d ?? {});
  } catch {}
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

// The page's league (web/proxy.ts copies its ?league= into a request header) on every server call, so a page and the
// layout above it read the same league. Outside a request (a test), or with no league named: home, the path as it was.
async function leagued(path: string): Promise<string> {
  try {
    return withLeague(path, (await headers()).get(LEAGUE_HEADER));
  } catch {
    return path;
  }
}

// The page this render is for (web/proxy.ts's header): where a refused call sends the visitor back to after signing in.
// Unreadable (outside a request): the start page.
async function pagePath(): Promise<string> {
  try {
    return (await headers()).get(PATH_HEADER) || "/";
  } catch {
    return "/";
  }
}

// The visitor's session, and only it, on every server call: the browser's cookies never reach the API otherwise.
// Outside a request (a test), or signed out: no cookie, and the API answers 401.
async function session(): Promise<Record<string, string>> {
  try {
    const name = sessionCookie();
    const v = (await cookies()).get(name)?.value;
    return v ? { cookie: `${name}=${v}` } : {};
  } catch {
    return {};
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  let r: Response;
  try {
    r = await fetch(`${API_BASE}${await leagued(path)}`, { headers: await session(), cache: "no-store", signal: AbortSignal.timeout(API_TIMEOUT_MS) });
  } catch (e) {
    unreachable(e);
  }
  return handle<T>(r);
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  let r: Response;
  try {
    r = await fetch(`${API_BASE}${await leagued(path)}`, {
      method: "POST", headers: { "content-type": "application/json", ...(await session()) }, body: JSON.stringify(body),
      cache: "no-store", signal: AbortSignal.timeout(API_TIMEOUT_MS),
    });
  } catch (e) {
    unreachable(e);
  }
  return handle<T>(r);
}

// The decision journal (GET /decisions). rationale: preset keys, null = still needs a reason.
export type Decision = {
  id: number; at: string; week: number | null; kind: string; source: "app" | "session" | "import"; title: string; decision: string;
  facts: Record<string, unknown> | null; rationale: string[] | null; rationale_note: string | null; followed_call: boolean | null;
  alternatives: string | null; exit_conditions: string | null; outcome: string | null; outcome_at: string | null;
  body: string | null; player_ids: number[] | null;
};
export type Decisions = { decisions: Decision[]; weeks: number[] };
export type Presets = Record<string, { key: string; label: string }[]>;
// What a move's answer carries: the row just recorded, and whether to ask for its reason.
export type DecisionRef = { id: number; needs_rationale: boolean };
// The watchlist (ffl/watch.py): GET /watch/ids, and POST /watch/{id} {watched}'s answer.
export type WatchIds = { ids: number[] };
export type WatchSet = { watched: boolean; ids: number[] };
// One of the Watch page's signal chips: lit while the signal is recent (`at`, its newest). `available` (the drop that freed
// him) comes only once it has fired; the four detectors' chips are on every row.
export type WatchChip = { kind: "ahead" | "usage" | "news" | "bench" | "available"; text: string | null; at: string | null; lit: boolean };
export type WatchRow = PlayerRow & { chips: WatchChip[] };
// GET /watch: the watched players grouped (moving: a signal in the last 3 days; quiet; elsewhere: on another roster).
// `ids`: the watchlist after the answer's own unwatch of anyone now on my roster; the page settles the stars from it.
export type WatchPage = {
  week: number; ros_week: number; stale_since: string | null; ids: number[];
  groups: { moving: WatchRow[]; quiet: WatchRow[]; elsewhere: WatchRow[] };
};

// The Settings switches (GET/POST /decisions/prefs): `log` records the app's moves and asks why; `page` shows the Decisions page.
export type DecisionPrefs = { log: boolean; page: boolean };
