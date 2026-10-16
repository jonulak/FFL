import type { ColumnDef } from "@tanstack/react-table";
import type { Form, PlayerRow, ViewSort } from "./api";

// Every figure the Players table can show as a column (ffl-8iz): its id (the URL's and ffl/player_views.py's
// COLUMNS, id for id, in the same order), the heading, the heading's hover, and what it reads off a row.
// The cells are drawn in components/waiver-table.tsx; this is the data a preset names.
export type ColumnId =
  | "add" | "wk" | "ros" | "ros_model" | "ros_spread" | "d_roster" | "d_pool" | "l3" | "beat" | "pace_gap" | "opp"
  | "snap_pct" | "tgt_snap" | "ypt" | "weight" | "air_share" | "air_catch" | "rz_tgt" | "rz_att" | "rz_pass" | "exp";
export type ColumnSpec = { id: ColumnId; label: string; title: string; get: (r: PlayerRow) => number | null | undefined };

const form = (key: keyof Form) => (r: PlayerRow) => (r.form ? (r.form[key] as number | null) : null);

export const PLAYER_COLUMNS: ColumnSpec[] = [
  { id: "add", label: "add", title: "projected points gained this season by adding him", get: (r) => r.add },
  { id: "wk", label: "wk", title: "this week's projection", get: (r) => r.week_proj },
  { id: "ros", label: "ROS", title: "usage-adjusted rest of season: the models' rate shrunk toward his last games by their snap-weighted count; what add runs on", get: (r) => r.ros_adj },
  { id: "ros_model", label: "model", title: "the models' own rest-of-season projection, before the usage adjustment", get: (r) => r.ros_proj },
  { id: "ros_spread", label: "ROS ±", title: "how far apart the rest-of-season sources are", get: (r) => r.ros_spread },
  // the two Δ titles are the player dialog's (components/season-facts.tsx)
  { id: "d_roster", label: "Δ roster", title: "ROS against my weakest at the position", get: (r) => r.delta_roster },
  { id: "d_pool", label: "Δ pool", title: "ROS against the league's replacement level", get: (r) => r.delta_pool },
  { id: "l3", label: "L3", title: "points per game over his last three games played", get: form("l3") },
  { id: "beat", label: "beat", title: "points per game over his last three games minus ESPN's projection for them", get: form("beat") },
  { id: "pace_gap", label: "pace", title: "his last three games' pace × games left (his bye out) minus the models' rest-of-season projection: if the last three held", get: form("pace_gap") },
  { id: "opp", label: "opp", title: "touches + targets per game over his last three (pass attempts for a QB; kicks for a K); the arrow is the latest game against the earlier ones", get: form("opp") },
  { id: "snap_pct", label: "snap", title: "share of the team's offensive snaps over his last three games (Sleeper; snaps land a day after the game)", get: form("snap_pct") },
  { id: "tgt_snap", label: "tgt/snap", title: "targets per offensive snap: the free stand-in for a route rate", get: form("tgt_snap") },
  { id: "ypt", label: "ypt", title: "receiving yards per target over the window", get: form("ypt") },
  { id: "weight", label: "wt", title: "what the window is worth to the adjusted ROS: each game's snap share against the position's lead players, a game at most (the prior is worth 4)", get: form("weight") },
  { id: "air_share", label: "air%", title: "share of his club's completed air yards: where he was caught, not where he was thrown to", get: form("air_share") },
  { id: "air_catch", label: "air/c", title: "completed air yards per catch (per completion for a QB)", get: form("air_catch") },
  { id: "rz_tgt", label: "rz tgt", title: "red-zone targets per game", get: form("rz_tgt") },
  { id: "rz_att", label: "rz car", title: "red-zone rushes per game", get: form("rz_att") },
  { id: "rz_pass", label: "rz att", title: "red-zone pass attempts per game", get: form("rz_pass") },
  { id: "exp", label: "exp", title: "years in the league (0 = rookie)", get: (r) => r.exp },
];
export const COLUMN_IDS: ColumnId[] = PLAYER_COLUMNS.map((c) => c.id);
const BY_ID = new Map(PLAYER_COLUMNS.map((c) => [c.id as string, c]));
export const columnSpec = (id: string): ColumnSpec | undefined => BY_ID.get(id);

// What each sortable column reads, as TanStack column defs without their cells (the table adds those).
// A missing number is `undefined`, which TanStack sorts last whichever way the column runs; every number
// sorts high first, the name A to Z.
const num = (c: ColumnSpec): ColumnDef<PlayerRow> => ({ id: c.id, accessorFn: (r) => c.get(r) ?? undefined, sortDescFirst: true, sortUndefined: "last" });
export const SORT_COLUMNS = {
  name: { id: "name", accessorFn: (r) => r.name, sortDescFirst: false, sortingFn: "text" } as ColumnDef<PlayerRow>,
  ...Object.fromEntries(PLAYER_COLUMNS.map((c) => [c.id, num(c)])),
} as Record<"name" | ColumnId, ColumnDef<PlayerRow>>;

// The sort a view can show: its own when that column is on the table (or the name); a hidden one falls
// to the first shown column, high first, and with none shown to the name.
export function sortFor(cols: string[], sort: ViewSort): ViewSort {
  if (sort.id === "name" || cols.includes(sort.id)) return sort;
  return cols.length ? { id: cols[0], desc: true } : { id: "name", desc: false };
}
