import type { PlayerLog, SeasonLog, WeekLog } from "./api";
import { injuryLabel, pts } from "./format";

// This season carries his snap share (player_card.with_snaps); a past season, a K and a D/ST do not.
export function hasSnaps(s: SeasonLog): boolean {
  return s.weeks.some((w) => w.snap_pct !== undefined);
}

// A week's collapsed-row cells: the position's stat group, then his snap share when the season
// carries it (a dash for a game Sleeper has no snaps for yet).
export function cells(w: WeekLog, snaps: boolean): [string, string][] {
  return snaps ? [...w.summary, ["SNAP", w.snap_pct == null ? "—" : `${w.snap_pct}%`]] : w.summary;
}

// The season modal's column headings: the position's stat group, read off the first week that has
// one (a bye or a missed game has none), else off the season's totals; SNAP after it when the
// season carries snaps.
export function columns(s: SeasonLog): string[] {
  const w = s.weeks.find((w) => w.summary.length > 0);
  const group = (w?.summary ?? s.totals?.summary ?? []).map(([label]) => label);
  return hasSnaps(s) ? [...group, "SNAP"] : group;
}

export const weekLabel =(w: WeekLog) => `W${w.week}`;

export const ptsText = (n: number | null) => pts(n);

// The current season's games and points, summed from its weeks rather than read from `totals`,
// so a week the live feed has laid over the log counts as it stands.
export function seasonPts(weeks: WeekLog[]): { games: number; pts: number } {
  const played = weeks.filter((w) => w.status === "played" || w.status === "live");
  return { games: played.length, pts: Math.round(played.reduce((t, w) => t + (w.pts ?? 0), 0) * 100) / 100 };
}

// The header's injury chip: the return week only once the current week is known (the season
// log brings it) and it is still ahead; until then the bare status, never a week already past.
export function headerInjury(status: string, returnWeek: number | null, week?: number): string {
  return week == null ? status : injuryLabel(status, returnWeek, week);
}

// A name that opens the season modal: plain type at rest, underlined under a pointer. Here, not in
// the client module, so server components (the play feed) can read the string.
export const NAME_LINK = "decoration-foreground/35 decoration-1 underline-offset-[0.2em] hover:underline";

// His rank at his position on this season's points to date, for the modal header; undefined until
// the log lands or when ESPN has no list for the position.
export function seasonRank(log: PlayerLog): number | undefined {
  return log.seasons.find((s) => s.season === log.season)?.totals?.pos_rank ?? undefined;
}
