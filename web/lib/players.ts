import type { FreeAgentRow, Owner, PlayerRow } from "./api";

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
