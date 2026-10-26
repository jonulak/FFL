import type { PlayerTx, TxMove, TxPlayer, TxSide } from "./api";

export type TxFilter = "all" | "waiver" | "free_agent" | "trade";

export const TX_FILTERS: { value: TxFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "waiver", label: "Waivers" },
  { value: "free_agent", label: "Free agents" },
  { value: "trade", label: "Trades" },
];

// The chip on a move: what kind of move it was, in the board's label style.
export const KIND_LABEL: Record<TxMove["kind"], string> = {
  waiver: "Waiver claim",
  free_agent: "Free agent",
  drop: "Drop",
  trade: "Trade",
};

// A lone drop belongs to no add filter; it shows under All and for its team.
export function filterMoves(moves: TxMove[], filter: TxFilter, mine: number | null): TxMove[] {
  return moves.filter((m) => (filter === "all" || m.kind === filter) && (mine == null || m.sides.some((s) => s.team_id === mine)));
}

// Moves under their fantasy week, newest week first; the API sends them newest first already.
export function byWeek(moves: TxMove[]): { week: number; moves: TxMove[] }[] {
  const out: { week: number; moves: TxMove[] }[] = [];
  for (const m of moves) {
    const last = out[out.length - 1];
    if (last && last.week === m.week) last.moves.push(m);
    else out.push({ week: m.week, moves: [m] });
  }
  return out;
}

// The newest `n` moves of those weeks, a week cut part way if need be; `count` stays the week's whole count, so
// its heading reads right while the rest of it is still to draw.
export function firstMoves(weeks: { week: number; moves: TxMove[] }[], n: number): { week: number; moves: TxMove[]; count: number }[] {
  const out: { week: number; moves: TxMove[]; count: number }[] = [];
  let left = n;
  for (const w of weeks) {
    if (left <= 0) break;
    out.push({ week: w.week, moves: w.moves.slice(0, left), count: w.moves.length });
    left -= w.moves.length;
  }
  return out;
}

export function weekLabel(week: number, current: number): string {
  if (week <= 0) return "Preseason";
  return week === current ? `Week ${week} · this week` : `Week ${week}`;
}

// A side's adds beside its drops, line for line: ESPN keeps no link between a claim and the cut
// it made room with, so two adds and two drops in one move pair in the order ESPN listed them.
export function pairs(side: Pick<TxSide, "added" | "dropped">): { added: TxPlayer | null; dropped: TxPlayer | null }[] {
  const n = Math.max(side.added.length, side.dropped.length);
  return Array.from({ length: n }, (_, i) => ({ added: side.added[i] ?? null, dropped: side.dropped[i] ?? null }));
}

export const HISTORY_LABEL: Record<PlayerTx["kind"], string> = {
  waiver: "Waiver claim",
  free_agent: "Free agent",
  drop: "Dropped",
  dropped: "Dropped",
  trade: "Traded",
  traded: "Traded",
  draft: "Drafted",
};

// The caption under a move: for a draft pick where it fell, else the other half of the move (who
// was cut for him, who he was cut for, or what went the other way in a trade and from whom).
// Null when there was no other half.
export function historyDetail(h: PlayerTx): string | null {
  if (h.kind === "draft") return h.round != null ? `Round ${h.round} · Pick ${h.pick} · #${h.overall} overall` : null;
  const names = h.with.join(", ");
  if (h.kind === "traded") return [h.from && `from ${h.from.team.trim()}`, names && `for ${names}`].filter(Boolean).join(" ");
  return names ? `for ${names}` : null;
}

// "Sep 16" off the log's local stamp: the season modal's history row keeps to the date.
export function shortDate(at: string): string {
  const d = new Date(at);
  return Number.isNaN(d.getTime()) ? at : d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

// The row's when: "Sep 18 · Wk 2", "Sep 7 · Draft"; a pick from ESPN's draft has no date, "Draft".
export function historyWhen(h: PlayerTx): string {
  const when = h.kind === "draft" ? "Draft" : h.week > 0 ? `Wk ${h.week}` : "Pre";
  return h.at ? `${shortDate(h.at)} · ${when}` : when;
}

// "Tue, Sep 16 · 3:05 AM" off the log's local stamp (the box's zone, which is the reader's).
export function stamp(at: string): string {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return at;
  const day = d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  const time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  return `${day} · ${time}`;
}
