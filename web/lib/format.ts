import type { Form } from "./api";

export function pts(n: number | null | undefined): string {
  return n == null ? "—" : n.toFixed(1);
}

export function signed(n: number): string {
  return n >= 0 ? `+${n.toFixed(1)}` : `−${Math.abs(n).toFixed(1)}`;
}

export function injuryTone(status: string): "ok" | "warn" | "bad" {
  if (status === "QUESTIONABLE" || status === "DOUBTFUL") return "warn";
  if (status === "OUT" || status === "INJURY_RESERVE" || status === "SUSPENSION") return "bad";
  return "ok";
}

// A player's name the way a broadcast app sets it in a narrow column: "B. Purdy", "A. St. Brown".
// A defence ("Ravens D/ST") and a one-word name stay as they are.
export function shortName(name: string): string {
  if (name.includes("D/ST")) return name;
  const [first, ...rest] = name.trim().split(/\s+/);
  return rest.length === 0 ? name : `${first[0]}. ${rest.join(" ")}`;
}


export type Trend = "up" | "down" | null;
export type FormItem = { key: keyof Form; label: string; value: string; title: string; trend?: Trend; note?: string };

// The direction the latest game's opportunity moved against the earlier ones; null when there
// is nothing to compare with or nothing moved.
export function oppTrend(f: Form): Trend {
  return f.opp_trend == null || f.opp_trend === 0 ? null : f.opp_trend > 0 ? "up" : "down";
}

// The seven form columns (STRATEGY.md "Recent form"), each with its hover definition, in the
// order the eye reads them: what he scored, against what was projected, the usage behind it,
// and where that pace lands against ROS. Nothing here is a verdict.
export const FORM_COLUMNS: { key: keyof Form; label: string; title: string }[] = [
  { key: "l3", label: "L3", title: "points per game over his last three games played" },
  { key: "beat", label: "beat", title: "those games' points minus ESPN's projection for them, per game" },
  { key: "opp", label: "opp", title: "touches + targets per game (pass attempts for a QB; kicks for a K); the arrow is the latest game against the earlier ones" },
  { key: "snap_pct", label: "snap", title: "share of the team's offensive snaps (Sleeper; snaps land a day after the game)" },
  { key: "tgt_snap", label: "tgt/snap", title: "targets per offensive snap over the games with snaps: the free stand-in for a route rate" },
  { key: "ypt", label: "ypt", title: "receiving yards per target over the window" },
  { key: "pace_gap", label: "pace", title: "L3 pace × games left (his bye out) minus his rest-of-season projection: if the last three held" },
];

// One form column's cell for a player: the number as the column shows it, and a note beneath
// when the number needs one (a short window's game count; how many games carry snaps). Null
// when his position has no such number or no game in the window has it yet.
export function formCell(f: Form, key: keyof Form): FormItem | null {
  const col = FORM_COLUMNS.find((c) => c.key === key)!;
  const item = (value: string, extra: Partial<FormItem> = {}): FormItem => ({ ...col, value, ...extra });
  switch (key) {
    case "l3": return item(pts(f.l3), {
      title: `points per game over his last ${f.games} played (weeks ${f.weeks.join(", ")})`,
      note: f.games < 3 ? `${f.games} game${f.games === 1 ? "" : "s"}` : undefined });
    case "beat": return item(signed(f.beat));
    case "opp": return f.opp == null ? null : item(f.opp.toFixed(1), { trend: oppTrend(f),
      title: col.title + (f.opp_trend == null ? "" : `; latest game ${signed(f.opp_trend)} on the earlier ones`) });
    case "snap_pct": return f.snap_pct == null ? null : item(`${f.snap_pct}%`, {
      title: `${col.title}, over ${f.snap_games} of the ${f.games} games`,
      note: f.snap_games < f.games ? `${f.snap_games} of ${f.games}` : undefined });
    case "tgt_snap": return f.tgt_snap == null ? null : item(f.tgt_snap.toFixed(2));
    case "ypt": return f.ypt == null ? null : item(f.ypt.toFixed(1));
    case "pace_gap": return item(signed(f.pace_gap));
    default: return null;
  }
}

// The same columns as one strip of label/value pairs, the columns he has no number for left
// out rather than dashed; the L label carries the game count when the window is short.
export function formItems(f: Form): FormItem[] {
  return FORM_COLUMNS.flatMap((c) => {
    const cell = formCell(f, c.key);
    return cell ? [c.key === "l3" ? { ...cell, label: `L${f.games}` } : cell] : [];
  });
}

// ESPN's injury status, plus the week the player is expected back while that is still ahead of us.
export function injuryLabel(status: string, returnWeek: number | null, week: number): string {
  return returnWeek != null && returnWeek > week ? `${status} · wk ${returnWeek}` : status;
}
