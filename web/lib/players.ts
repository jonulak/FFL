import type { FreeAgentRow, Owner, PlayerRow } from "./api";

// The Players URL: the trimmed query, the tab, and fa=0 only when the Free agents toggle is off
// (absent means on, so every old link and a bare /players open on the waiver view).
export function playersHref(q: string, pos: string, fa: boolean): string {
  const params = new URLSearchParams();
  if (q.trim()) params.set("q", q.trim());
  params.set("pos", pos);
  if (!fa) params.set("fa", "0");
  return `/players?${params}`;
}

export type RowKind = "free" | "theirs" | "mine";
export function rowKind(r: Pick<PlayerRow, "owner" | "mine">): RowKind {
  return r.mine ? "mine" : r.owner ? "theirs" : "free";
}

export function isFreeAgent(r: PlayerRow): r is FreeAgentRow {
  return r.owner == null;
}

// The owner chip's text: the team's abbreviation, its name when ESPN gave none, else a plain word.
export function ownerLabel(o: Owner): string {
  return o.abbrev ?? o.name ?? "rostered";
}
