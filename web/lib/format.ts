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

import type { Form } from "./api";

// The recent-form strip (STRATEGY.md "Recent form") as label/value pairs, in the order the eye
// reads them: what he scored, against what was projected, the usage behind it, and where that
// pace lands against ROS. A column his position has no number for, or no game has yet, is left
// out rather than shown as a dash. `l3` carries the game count when the window is short.
export function formItems(f: Form): { label: string; value: string; title: string }[] {
  const out = [
    { label: `L${f.games}`, value: pts(f.l3),
      title: `points per game over his last ${f.games} played (weeks ${f.weeks.join(", ")})` },
    { label: "beat", value: signed(f.beat), title: "those games' points minus ESPN's projection for them, per game" },
  ];
  if (f.opp != null) {
    const trend = f.opp_trend == null ? "" : f.opp_trend > 0 ? " ↑" : f.opp_trend < 0 ? " ↓" : "";
    out.push({ label: "opp", value: `${f.opp.toFixed(1)}${trend}`,
      title: "touches + targets per game (pass attempts for a QB; kicks for a K)" +
        (f.opp_trend == null ? "" : `; latest game ${signed(f.opp_trend)} on the earlier ones`) });
  }
  if (f.snap_pct != null) {
    out.push({ label: "snap", value: `${f.snap_pct}%`,
      title: `share of the team's offensive snaps over ${f.snap_games} of the ${f.games} games (Sleeper; snaps land a day after the game)` });
  }
  if (f.tgt_snap != null) out.push({ label: "tgt/snap", value: f.tgt_snap.toFixed(2), title: "targets per offensive snap over the games with snaps: the free stand-in for a route rate" });
  if (f.ypt != null) out.push({ label: "ypt", value: f.ypt.toFixed(1), title: "receiving yards per target over the window" });
  out.push({ label: "pace", value: signed(f.pace_gap), title: "L3 pace × games left (his bye out) minus his rest-of-season projection: if the last three held" });
  return out;
}

// ESPN's injury status, plus the week the player is expected back while that is still ahead of us.
export function injuryLabel(status: string, returnWeek: number | null, week: number): string {
  return returnWeek != null && returnWeek > week ? `${status} · wk ${returnWeek}` : status;
}
