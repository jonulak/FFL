import type { BoxItem, Field, GameState, League, Player, Roster, ScoreSide, ScoreStarter, Side, TeamRoster, Volume, Week } from "./api";
import { gameLeft } from "./game";

// The live overlay laid on in the browser: ffl/live.py's overlay_* ported, so a /live tick patches the page
// instead of refetching it whole. The stream sends, per tick, what changed for its league since it last
// sent (live.delta); `merge` keeps the sum of those for the week and `overlay*` lays it on any payload of
// that week, the one the page holds and every full fetch after it (a fetch built before a delta must not
// undo it). tests/test_live_overlay_fixtures.py writes overlay.fixtures.json off the Python and
// overlay.test.ts checks this against it.

// A game state as the feed has it: "pre" too (the page's GameState is only ever an in-progress or final one).
type FeedGame = Omit<GameState, "status"> & { status: string };
// A player's stat-derived fields, off his feed row (live.stat_line, live.volume, live.box_score).
type PlayerLive = { stat_line: string | null; volume: Volume | null; box: BoxItem[] | null };

// One /live JSON message: `week` alone means a new week (refetch).
export type LiveDelta = {
  v: number; week: number; points?: Record<string, number>; games?: Record<string, FeedGame>;
  players?: Record<string, PlayerLive>; sigma?: Record<string, number>;
};
export type LiveState = {
  week: number; points: Record<string, number>; games: Record<string, FeedGame>; players: Record<string, PlayerLive>;
  sigma: Record<string, number> | null;
};

const fresh = (week: number): LiveState => ({ week, points: {}, games: {}, players: {}, sigma: null });

// The week's state with one delta folded in; `refetch` when the delta opens a week the state was not on.
export function merge(acc: LiveState | null, d: LiveDelta): { live: LiveState; refetch: boolean } {
  if (!d.points || (acc && acc.week !== d.week)) return { live: fresh(d.week), refetch: true };
  const a = acc ?? fresh(d.week);
  return {
    live: {
      week: d.week, points: { ...a.points, ...d.points }, games: { ...a.games, ...d.games },
      players: { ...a.players, ...d.players }, sigma: d.sigma ?? a.sigma,
    },
    refetch: false,
  };
}

const round = (n: number, d: number) => Math.round(n * 10 ** d) / 10 ** d;
const onField = (g: FeedGame) => g.status === "in" || g.status === "post";

// live.field
export function field(pos: string, team: string | null, game: FeedGame): Field | null {
  if (!game.ball || !team) return null;
  if (pos === "D/ST" ? game.ball !== team : game.ball === team) return game.red_zone ? "red_zone" : "on";
  return null;
}

// live.overlay_players
function players<P extends Player>(ps: P[], live: LiveState): P[] {
  return ps.map((p) => {
    const q: P = { ...p };
    const id = String(p.player_id);
    if (id in live.points) q.actual = live.points[id];
    const g = live.games[p.team];
    if (g && onField(g)) {
      q.game = g as GameState;
      const f = field(p.pos ?? "", p.team, g);
      if (f) q.field = f; else delete q.field;
    }
    const x = live.players[id];
    if (x) {
      if (x.stat_line) q.stat_line = x.stat_line; else delete q.stat_line;
      if (x.volume) q.volume = x.volume; else delete q.volume;
      if (x.box) q.box = x.box;
    }
    return q;
  });
}

// season.playable_proj, game_left, side_outlook, win_prob
const UNPLAYABLE = new Set(["OUT", "INJURY_RESERVE", "SUSPENSION"]);
const SIGMA_FALLBACK: Record<string, number> = { QB: 7.5, RB: 7.0, WR: 7.0, TE: 5.5, K: 4.0, "D/ST": 5.5 };
const SIGMA_DEFAULT = 6.0;
type Priced = Pick<ScoreStarter, "pos" | "proj" | "actual" | "game_played" | "bye" | "injury"> & { game?: FeedGame | GameState };

const playable = (p: Priced) => (p.bye || UNPLAYABLE.has(p.injury) ? 0 : p.proj || 0);

function left(p: Priced): number {
  const g = p.game;
  if (g?.status === "in") return gameLeft(g as GameState) ?? 1;
  return g?.status === "post" || (p.game_played || 0) >= 100 ? 0 : 1;
}

function outlook(lineup: Priced[], sigma: Record<string, number>): [number, number] {
  let mean = 0, v = 0;
  for (const p of lineup) {
    const actual = p.actual || 0, proj = playable(p);
    let l = left(p);
    if (l === 1 && actual > 0 && !p.game) l = proj > 0 ? Math.max(0, proj - actual) / proj : 0;
    mean += actual + proj * l;
    if (proj > 0) {
      const sd = sigma[p.pos] ?? SIGMA_FALLBACK[p.pos] ?? SIGMA_DEFAULT;
      v += sd * sd * l;
    }
  }
  return [mean, v];
}

// Abramowitz-Stegun 7.1.26 (error < 1.5e-7): JS has no erf, and the meter shows whole percents
function erf(x: number): number {
  const s = Math.sign(x), t = 1 / (1 + 0.3275911 * Math.abs(x));
  const y = 1 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
  return s * y;
}

export function winProb(a: Priced[], b: Priced[], sigma: Record<string, number>): number {
  const [ma, va] = outlook(a, sigma);
  const [mb, vb] = outlook(b, sigma);
  if (va + vb <= 0) {
    // the score as the board shows it, so float noise in the sums never splits a tie
    const x = round(ma, 2), y = round(mb, 2);
    return x > y ? 1 : x < y ? 0 : 0.5;
  }
  return 0.5 * (1 + erf((ma - mb) / Math.sqrt(2 * (va + vb))));
}

const odds = (a: Priced[], b: Priced[], live: LiveState) => round(winProb(a, b, live.sigma ?? SIGMA_FALLBACK), 3);

// live.overlay_side
function side<P extends Player>(s: Side<P>, live: LiveState): Side<P> {
  const out = { ...s, lineup: players(s.lineup, live), bench: players(s.bench, live), ir: players(s.ir, live) };
  out.actual_total = round(out.lineup.reduce((t, p) => t + p.actual, 0), 2);
  return out;
}

// Each overlay leaves a payload of another week as it is (api._live_for).
export function overlayWeek(w: Week, live: LiveState | null): Week {
  if (!live || w.week !== live.week) return w;
  const out = { ...w, me: side(w.me, live), opp: w.opp && side(w.opp, live) };
  if (w.win_prob && out.opp) {
    const p = odds(out.me.lineup, out.opp.lineup, live);
    out.win_prob = { ...w.win_prob, me: p, opp: round(1 - p, 3) };
  }
  return out;
}

export function overlayRoster(r: Roster, live: LiveState | null): Roster {
  if (!live || r.week !== live.week) return r;
  return { ...r, cards: players(r.cards, live) };
}

export function overlayTeam(t: TeamRoster, live: LiveState | null): TeamRoster {
  if (!live || t.week !== live.week) return t;
  return { ...t, side: side(t.side, live) };
}

// season.matchup_final
function final(m: { home: ScoreSide; away: ScoreSide }, games: Record<string, FeedGame>): boolean {
  const sides = [m.home, m.away];
  if (!sides.every((s) => s.starter_ids?.length)) return false;
  if (sides.some((s) => (s.starter_teams ?? []).some((t) => games[t]?.status === "in"))) return false;
  return sides.every((s) => (s.pending_teams ?? []).every((t) => games[t]?.status === "post"));
}

// live.overlay_league. shortcut: a side's score re-sums its starters' shown points, where the Python sums the
// feed's alone (a starter the feed lacks counts 0 there, ESPN's number here); they differ only for a starter
// ESPN credits that the feed never scored. Upgrade if a tile and its board ever disagree.
export function overlayLeague(l: League, live: LiveState | null): League {
  if (!live || l.week !== live.week) return l;
  const scoreboard = l.scoreboard.map((m) => {
    const row = { ...m };
    for (const key of ["home", "away"] as const) {
      let s = m[key];
      const ids = s.starter_ids ?? [];
      if (s.starters?.length) {
        const starters = s.starters.map((p) => {
          const q = { ...p, actual: live.points[String(p.player_id)] ?? p.actual };
          const g = live.games[p.team];
          if (g && onField(g)) q.game = g as GameState;
          return q;
        });
        s = { ...s, starters };
        if (ids.some((i) => String(i) in live.points)) {
          const by = new Map(starters.map((p) => [p.player_id, p.actual]));
          s.score = round(ids.reduce((t, i) => t + (live.points[String(i)] ?? by.get(i) ?? 0), 0), 2);
        }
      }
      row[key] = s;
    }
    row.live = (["home", "away"] as const).some((k) => (m[k].starter_teams ?? []).some((t) => live.games[t]?.status === "in"));
    row.final = final(m, live.games);
    if (m.win_prob) {
      const p = odds(row.away.starters, row.home.starters, live);
      row.win_prob = { away: p, home: round(1 - p, 3) };
    }
    return row;
  });
  return { ...l, scoreboard };
}
