import type { NflDrive, NflGame, NflPlay, NflStarter } from "./api";

// The NFL tab's routes: a game by its week and either club (ESPN's abbreviation), its full feed beneath it.
export const gameHref = (week: number, club: string) => `/nfl/${week}/${club}`;

// Where a player's game line goes: his club's game that week; a bye, or no game, goes nowhere.
export function gameLinkFor(p: { bye: boolean; opponent?: string | null; team: string }, week: number): string | null {
  return p.bye || !p.opponent ? null : gameHref(week, p.team);
}
export type FeedFilter = "all" | "mine" | "scoring";
// The game page with its play feed filtered (the feed is part of the game page; `/plays` only redirects here).
export const feedHref = (week: number, club: string, f: FeedFilter = "all") =>
  `${gameHref(week, club)}${f === "all" ? "" : `?f=${f}`}`;

export function parseFilter(raw: string | string[] | undefined): FeedFilter {
  const v = Array.isArray(raw) ? raw[0] : raw;
  return v === "mine" || v === "scoring" ? v : "all";
}

export function parseGameParams(week: string, team: string): { week: number; team: string } | null {
  const w = Number(week);
  return Number.isInteger(w) && w > 0 && /^[A-Za-z]{2,3}$/.test(team) ? { week: w, team: team.toUpperCase() } : null;
}

// The feed under a filter: My players is my starters' plays only (the opponent's are marked under All); a drive
// the filter empties goes.
export function filterDrives(drives: NflDrive[], f: FeedFilter): NflDrive[] {
  if (f === "all") return drives;
  const keep = (p: NflPlay) => (f === "scoring" ? p.scoring : p.mine.length > 0);
  return drives.map((d) => ({ ...d, plays: d.plays.filter(keep) })).filter((d) => d.plays.length > 0);
}

// The running score after a play in one fixed order, away then home: "CHI 7" · "GB 14". `scorer` says which part
// the scoring club owns (full ink): the side whose score rose since `prev` (the score before the play, 0-0 when
// none is given), which is right on a pick-six or a safety; the offence when the change cannot be read; neither
// for a play with no club.
export function scoreLine(
  p: NflPlay, g: NflGame, prev?: { away: number | null; home: number | null } | null,
): { away: string; home: string; scorer: "away" | "home" | null } {
  const { away, home } = p.score;
  const base = prev ?? { away: 0, home: 0 };
  const awayUp = (away ?? 0) > (base.away ?? 0);
  const homeUp = (home ?? 0) > (base.home ?? 0);
  const byScore = awayUp !== homeUp ? (awayUp ? "away" : "home") : null;
  const byOffence = p.team === g.away.abbr ? "away" : p.team === g.home.abbr ? "home" : null;
  return { away: `${g.away.abbr} ${away ?? 0}`, home: `${g.home.abbr} ${home ?? 0}`, scorer: byScore ?? byOffence };
}

export function gameState(g: NflGame): string {
  if (g.detail && /postpon|delay|suspend|cancel/i.test(g.detail)) return g.detail.toUpperCase();
  if (g.status === "post") return (g.quarter ?? 0) >= 5 ? "FINAL/OT" : "FINAL";
  if (g.status === "in") {
    if (g.detail && /half/i.test(g.detail)) return "HALF";
    const q = g.quarter ?? 0;
    return `${q >= 5 ? "OT" : `Q${q}`}${g.clock ? ` ${g.clock}` : ""}`;
  }
  return (g.detail ?? "").toUpperCase();
}

const NIGHT: Record<string, string> = { Thu: "TNF", Sun: "SNF", Mon: "MNF" };

// A kickoff window's heading in the viewer's zone: "SUN 1:00 PM", the prime-time slots by their names.
export function windowLabel(iso: string, tz?: string): string {
  const d = new Date(iso);
  const parts = new Intl.DateTimeFormat("en-US", { weekday: "short", hour: "numeric", minute: "2-digit", hour12: true, timeZone: tz }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  const hour24 = Number(new Intl.DateTimeFormat("en-US", { hour: "numeric", hour12: false, timeZone: tz }).format(d));
  const day = get("weekday");
  if (hour24 >= 19 && NIGHT[day]) return NIGHT[day];
  return `${day} ${get("hour")}:${get("minute")} ${get("dayPeriod")}`.toUpperCase();
}

// The In-this-game rows: my starters down the left, the opponent's down the right, paired by order.
export function inGameRows(s: { me: NflStarter[]; opp: NflStarter[] }): [NflStarter | undefined, NflStarter | undefined][] {
  return Array.from({ length: Math.max(s.me.length, s.opp.length) }, (_, i) => [s.me[i], s.opp[i]]);
}

// Each scoring play's score before it: the next-older scoring play's (plays are newest first), none for the
// game's first. Feeds `scoreLine`'s `prev`.
export function priorScores(plays: NflPlay[]): Record<string, NflPlay["score"] | null> {
  const sc = plays.filter((p) => p.scoring);
  return Object.fromEntries(sc.map((p, i) => [p.id, sc[i + 1]?.score ?? null]));
}
