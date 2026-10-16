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

// A board's whole play feed for the board's own week (a finished week's board has one too): my game
// plain, `&team=` (the board's left team) for any other.
export function playsHref(mine: boolean, left: number, week: number): string {
  return mine ? `/plays?week=${week}` : `/plays?week=${week}&team=${left}`;
}

// The way back from a game's plays to its board: the Matchup page for mine this week, else the League
// board (`/league/<week>/<left team>`), which serves my game of a finished week too.
export function boardHref(mine: boolean, week: number | null, left: number | null, current: boolean): string {
  return (mine && current) || week == null || left == null ? "/" : `/league/${week}/${left}`;
}

// A positive integer off the plays page's search params (`?team=`, `?week=`), else null.
function parsePositive(raw: string | string[] | undefined): number | null {
  const v = Array.isArray(raw) ? raw[0] : raw;
  const t = Number(v);
  return v && Number.isInteger(t) && t > 0 ? t : null;
}

// `?team=`: null is my game.
export const parseTeam = parsePositive;
// `?week=`: null is the current week.
export const parseWeek = parsePositive;
