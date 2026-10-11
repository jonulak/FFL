import type { NflDrive, NflGame, NflPlay } from "./api";

// The NFL tab's routes: a game by its week and either club (ESPN's abbreviation), its full feed beneath it.
export const gameHref = (week: number, club: string) => `/nfl/${week}/${club}`;
export type FeedFilter = "all" | "mine" | "scoring";
export const feedHref = (week: number, club: string, f: FeedFilter = "all") =>
  `${gameHref(week, club)}/plays${f === "all" ? "" : `?f=${f}`}`;

export function parseFilter(raw: string | string[] | undefined): FeedFilter {
  const v = Array.isArray(raw) ? raw[0] : raw;
  return v === "mine" || v === "scoring" ? v : "all";
}

export function parseGameParams(week: string, team: string): { week: number; team: string } | null {
  const w = Number(week);
  return Number.isInteger(w) && w > 0 && /^[A-Za-z]{2,3}$/.test(team) ? { week: w, team: team.toUpperCase() } : null;
}

// The feed under a filter: My players is either side's marks (both are drawn); a drive the filter empties goes.
export function filterDrives(drives: NflDrive[], f: FeedFilter): NflDrive[] {
  if (f === "all") return drives;
  const keep = (p: NflPlay) => (f === "scoring" ? p.scoring : p.mine.length > 0 || p.opp.length > 0);
  return drives.map((d) => ({ ...d, plays: d.plays.filter(keep) })).filter((d) => d.plays.length > 0);
}

// The running score after a play, the scoring club's number first: "GB 14–7".
export function scoreLine(p: NflPlay, g: NflGame): string {
  const { away, home } = p.score;
  if (p.team === g.home.abbr) return `${g.home.abbr} ${home ?? 0}–${away ?? 0}`;
  if (p.team === g.away.abbr) return `${g.away.abbr} ${away ?? 0}–${home ?? 0}`;
  return `${g.away.abbr} ${away ?? 0}–${home ?? 0} ${g.home.abbr}`;
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
