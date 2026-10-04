import type { League } from "./api";

// Where a League scoreboard tile goes: that game's board, named by its week and the away team
// (the side the board puts on the left). A week beyond the current one has pairings but no
// lineups yet, so its tiles go nowhere.
export function matchupHref(m: League["scoreboard"][number], week: number, current: number): string | null {
  return week <= current ? `/league/${week}/${m.away.team_id}` : null;
}

// The route's two segments back as numbers; null when either is not a positive integer.
export function parseMatchupParams(week: string, team: string): { week: number; team: number } | null {
  const w = Number(week);
  const t = Number(team);
  return Number.isInteger(w) && w > 0 && Number.isInteger(t) && t > 0 ? { week: w, team: t } : null;
}

// A board's whole play feed: /plays for my game, `?team=` (the board's left team) for any other.
export function playsHref(mine: boolean, left: number): string {
  return mine ? "/plays" : `/plays?team=${left}`;
}

// The way back from a game's plays to its board: the Matchup page for mine, the League board
// (`/league/<week>/<left team>`) for any other.
export function boardHref(mine: boolean, week: number | null, left: number | null): string {
  return mine || week == null || left == null ? "/" : `/league/${week}/${left}`;
}

// `?team=` off the plays page's search params: a positive integer, else my game.
export function parseTeam(raw: string | string[] | undefined): number | null {
  const v = Array.isArray(raw) ? raw[0] : raw;
  const t = Number(v);
  return v && Number.isInteger(t) && t > 0 ? t : null;
}
