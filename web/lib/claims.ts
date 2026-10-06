// "Get K of N" claim groups, pure (ffl/claims.py is the truth; these are the edits the panel makes
// and the labels it shows). A group gets min(rows, targets) of its targets, best first, where the
// rows are its open roster spots (first) and its cuts (first to go first). Every edit returns new
// groups; nothing here mutates its input.
import type { ClaimGroup, ClaimPlayer, PoolStatus, SubmitResult } from "./api";

type List = "targets" | "cuts";
type Status = Record<string, PoolStatus>;

const copy = (groups: ClaimGroup[]): ClaimGroup[] => groups.map((g) => ({ ...g, targets: [...g.targets], cuts: [...g.cuts] }));
// A group an edit empties (no targets, no cuts) is gone; one with only cuts is kept (he is still
// choosing targets). Only the groups the edit touched are dropped, so a group just made empty from
// the panel's "+ new group" waits for its first target.
const isEmpty = (g: ClaimGroup) => !g.targets.length && !g.cuts.length;
const dropEmptied = (before: ClaimGroup[], after: ClaimGroup[]): ClaimGroup[] =>
  after.filter((g, i) => !isEmpty(g) || isEmpty(before[i]));

function arrayMove<T>(xs: T[], from: number, to: number): T[] {
  const out = [...xs];
  const [x] = out.splice(from, 1);
  out.splice(to, 0, x);
  return out;
}

export const capacity = (g: ClaimGroup): number => g.cuts.length + g.open;

// "WR" when every target plays one position, "mixed" when they span several, "" when there are none.
export function groupPos(g: ClaimGroup, pos: Map<number, string>): string {
  const ps = new Set(g.targets.map((t) => pos.get(t) ?? "?"));
  return ps.size === 0 ? "" : ps.size === 1 ? [...ps][0] : "mixed";
}

// The row control's label: his position and rank in his group ("WR·2"); null when he is no target.
export function rowTag(id: number, groups: ClaimGroup[], pos: Map<number, string>): string | null {
  for (const g of groups) {
    const i = g.targets.indexOf(id);
    if (i >= 0) return `${pos.get(id) ?? "?"}·${i + 1}`;
  }
  return null;
}

// A player is a target in one group: adding him anywhere takes him out of every group first.
export function addTarget(groups: ClaimGroup[], gi: number | "new", id: number): ClaimGroup[] {
  const out = copy(groups).map((g) => ({ ...g, targets: g.targets.filter((t) => t !== id) }));
  if (gi === "new") return [...dropEmptied(groups, out), { targets: [id], cuts: [], open: 0 }];
  out[gi].targets.push(id);
  return dropEmptied(groups, out);
}

// A roster player is a cut in one group: adding him takes him out of every other.
export function addCut(groups: ClaimGroup[], gi: number, id: number): ClaimGroup[] {
  const out = copy(groups).map((g) => ({ ...g, cuts: g.cuts.filter((c) => c !== id) }));
  out[gi].cuts.push(id);
  return dropEmptied(groups, out);
}

export function removeFrom(groups: ClaimGroup[], gi: number, list: List, id: number): ClaimGroup[] {
  const out = copy(groups);
  out[gi][list] = out[gi][list].filter((x) => x !== id);
  return dropEmptied(groups, out);
}

export const newGroup = (groups: ClaimGroup[]): ClaimGroup[] => [...copy(groups), { targets: [], cuts: [], open: 0 }];
export const removeGroup = (groups: ClaimGroup[], gi: number): ClaimGroup[] => copy(groups).filter((_, i) => i !== gi);

// The weekday the claims process: the earliest waiver date among the targets, Wednesday (the
// league's weekly run) when none is known.
export function processDay(dates: (number | null)[]): string {
  const ds = dates.filter((d): d is number => d != null);
  return ds.length ? new Date(Math.min(...ds)).toLocaleDateString("en-US", { weekday: "short" }) : "Wed";
}

export function move(groups: ClaimGroup[], gi: number, list: List, from: number, to: number): ClaimGroup[] {
  const out = copy(groups);
  out[gi][list] = arrayMove(out[gi][list], from, to);
  return out;
}

export const moveGroup = (groups: ClaimGroup[], from: number, to: number): ClaimGroup[] => arrayMove(copy(groups), from, to);

// Open spots belong to one group, which uses all of them: switching one group on switches every other off.
export function setOpen(groups: ClaimGroup[], gi: number, on: boolean, openSpots: number): ClaimGroup[] {
  return copy(groups).map((g, i) => ({ ...g, open: i === gi ? (on ? openSpots : 0) : on ? 0 : g.open }));
}

// "At most: 2 WR, 1 TE": what the week's claims can land, by position (a mixed group by its positions).
export function atMost(groups: ClaimGroup[], pos: Map<number, string>): string {
  const n = new Map<string, number>();
  for (const g of groups) {
    const k = Math.min(capacity(g), g.targets.length);
    if (!k) continue;
    const p = groupPos(g, pos);
    const key = p === "mixed" ? `of ${[...new Set(g.targets.map((t) => pos.get(t) ?? "?"))].join("/")}` : p;
    n.set(key, (n.get(key) ?? 0) + k);
  }
  return n.size ? `At most: ${[...n].map(([p, k]) => `${k} ${p}`).join(", ")}` : "";
}

const isFA = (id: number, status: Status) => status[String(id)] === "FREEAGENT";

export const groupHasFA = (g: ClaimGroup, status: Status): boolean => g.targets.some((t) => isFA(t, status));

// The order the panel shows: free agents first (they are added on Submit, ahead of any claim), then
// the waiver targets, each block in rank order. Unknown status counts as waivers.
export function displayTargets(g: ClaimGroup, status: Status): { id: number; now: boolean }[] {
  const fa = g.targets.filter((t) => isFA(t, status));
  const w = g.targets.filter((t) => !isFA(t, status));
  return [...fa.map((id) => ({ id, now: true })), ...w.map((id) => ({ id, now: false }))];
}

// A drag inside one block (free agents, or waiver targets) rebuilds the stored order as FA block + waiver block.
export function reorderTargets(g: ClaimGroup, status: Status, faBlock: boolean, from: number, to: number): number[] {
  const fa = g.targets.filter((t) => isFA(t, status));
  const w = g.targets.filter((t) => !isFA(t, status));
  return faBlock ? [...arrayMove(fa, from, to), ...w] : [...fa, ...arrayMove(w, from, to)];
}

// "FA" (added the moment you submit), "W·Wed" (on waivers, clears that day, local time), "W" with no
// date, "" when the status is unknown.
export function statusTag(p: { status: PoolStatus | null; waiver_date: number | null } | undefined): string {
  if (!p?.status) return "";
  if (p.status === "FREEAGENT") return "FA";
  return p.waiver_date ? `W·${new Date(p.waiver_date).toLocaleDateString("en-US", { weekday: "short" })}` : "W";
}

// Why a roster player cannot be a cut, or null. A locked cut (his game started) only blocks an
// instant add; a claim processes after the games.
export function cutBlock(p: ClaimPlayer, groupHasFA: boolean): string | null {
  if (p.slot === "IR") return "on IR";
  if (p.droppable === false) return "undroppable";
  if (groupHasFA && p.locked) return "game started";
  return null;
}

// The Submit dialog's claims verdict. `planned` is the claim count frozen when Send was pressed;
// `keptOutside` the claims filed on ESPN directly that were kept (they run ahead of ours). Never ok
// unless the server matched with no error and a read-back; a failed read-back says so, not "holds 0".
export function submitSummary(
  r: Pick<SubmitResult, "sent" | "pending" | "match" | "error" | "readback_failed">, planned: number, keptOutside = 0,
): { ok: boolean; text: string } {
  const n = r.sent.length;
  if (r.match && !r.error && !r.readback_failed) {
    if (!planned) return { ok: true, text: "No claims to send" };
    return keptOutside
      ? { ok: true, text: `ESPN holds all ${n} of yours, in this order · plus ${keptOutside} filed on ESPN directly, ahead of these` }
      : { ok: true, text: `ESPN holds all ${n}, in this order` };
  }
  if (r.readback_failed) return { ok: false, text: `Sent ${n}, but couldn't read ESPN back` };
  const held = new Set(r.pending.map((p) => p.id));
  const k = r.sent.filter((c) => held.has(c.id)).length;
  if (!n) return { ok: false, text: planned ? `Nothing was sent: ESPN holds 0 of ${planned}` : "Nothing was sent" };
  return { ok: false, text: k === n && n === planned ? `ESPN holds all ${n}, in a different order` : `ESPN holds ${k} of ${planned}` };
}

// A submit the API refused, sorted for the dialog: 409 one is running, or (an object detail) the
// statuses or ESPN's week moved since the review; 422 the plan's reasons, 502 ESPN's login,
// anything else the readable line. `status` is null for a failure that never reached the API.
export type SubmitFailure =
  | { kind: "running" }
  | { kind: "changed"; error: string }
  | { kind: "invalid"; errors: { group: number; player_id: number | null; reason: string }[] }
  | { kind: "espn"; error: string }
  | { kind: "other"; error: string };

export function submitFailure(status: number | null, body: unknown, message: string): SubmitFailure {
  const detail = (body as { detail?: unknown } | null)?.detail as { errors?: unknown; error?: string } | string | undefined;
  if (status === 409)
    return detail && typeof detail === "object" && detail.error ? { kind: "changed", error: detail.error } : { kind: "running" };
  if (status === 422 && detail && typeof detail === "object" && Array.isArray(detail.errors))
    return { kind: "invalid", errors: detail.errors as Extract<SubmitFailure, { kind: "invalid" }>["errors"] };
  if (status === 502) return { kind: "espn", error: (typeof detail === "object" && detail?.error) || message };
  return { kind: "other", error: message };
}
