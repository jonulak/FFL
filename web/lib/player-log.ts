import type { SeasonLog, WeekLog } from "./api";
import { pts } from "./format";

// The season modal's column headings: the position's stat group, read off the first week that has
// one (a bye or a missed game has none), else off the season's totals.
export function columns(s: SeasonLog): string[] {
  const w = s.weeks.find((w) => w.summary.length > 0);
  return (w?.summary ?? s.totals?.summary ?? []).map(([label]) => label);
}

export const weekLabel = (w: WeekLog) => `W${w.week}`;

export const ptsText = (n: number | null) => pts(n);

// The current season's games and points, summed from its weeks rather than read from `totals`,
// so a week the live feed has laid over the log counts as it stands.
export function seasonPts(weeks: WeekLog[]): { games: number; pts: number } {
  const played = weeks.filter((w) => w.status === "played" || w.status === "live");
  return { games: played.length, pts: Math.round(played.reduce((t, w) => t + (w.pts ?? 0), 0) * 100) / 100 };
}
