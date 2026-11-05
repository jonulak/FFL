// "Get K of N" claim groups, pure (ffl/claims.py is the truth; these are the edits the panel makes
// and the labels it shows). A group gets min(rows, targets) of its targets, best first, where the
// rows are its open roster spots (first) and its cuts (first to go first). Every edit returns new
// groups; nothing here mutates its input.
import type { ClaimErr, ClaimGroup, ClaimPlayer, PoolStatus, SubmitResult } from "./api";

type List = "targets" | "cuts";
type Status = Record<string, PoolStatus>;

const copy = (groups: ClaimGroup[]): ClaimGroup[] => groups.map((g) => ({ ...g, targets: [...g.targets], cuts: [...g.cuts] }));
// A group an edit empties (no targets, no cuts) is gone; one with only cuts is kept (he is still
// choosing targets). Only the groups the edit touched are dropped, so a group just made empty from
// the panel's "+ new group" waits for its first target.
const isEmpty = (g: ClaimGroup) => !g.targets.length && !g.cuts.length;
const dropEmptied = (before: ClaimGroup[], after: ClaimGroup[]): ClaimGroup[] =>
  after.filter((g, i) => !isEmpty(g) || isEmpty(before[i]));
// A drag keeps the group it empties (renumbering the groups under the pointer would move its
// target), so the drop prunes against the plan as it stood when the drag began.
export const prune = dropEmptied;

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

// A roster player can be a cut in several groups (claims sharing a drop exclude each other, so it
// reads "drop him for group 1, else for group 2"), once in each.
export function addCut(groups: ClaimGroup[], gi: number, id: number): ClaimGroup[] {
  const out = copy(groups);
  if (!out[gi].cuts.includes(id)) out[gi].cuts.push(id);
  return out;
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

// Groups carry no id, so the panel keys its cards here: a group unchanged keeps its key wherever it
// moved (a reorder animates, a delete shifts no state), an edited one the key at its index, a new
// one a fresh key from `next`.
export function regroupKeys(before: ClaimGroup[], keys: number[], after: ClaimGroup[], next: number): { keys: number[]; next: number } {
  const sig = (g: ClaimGroup) => JSON.stringify(g);
  const pool = new Map<string, number[]>();
  before.forEach((g, i) => pool.set(sig(g), [...(pool.get(sig(g)) ?? []), keys[i]]));
  const out: (number | undefined)[] = after.map((g) => pool.get(sig(g))?.shift());
  const used = new Set(out);
  return {
    keys: out.map((k, i) => {
      if (k != null) return k;
      const mine = keys[i];
      if (mine != null && !used.has(mine)) { used.add(mine); return mine; }
      return next++;
    }),
    next,
  };
}

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

// The targets in rank order, each flagged `now` when a free agent (added on Submit, not claimed; a waiver
// target ranked above him keeps his row). Unknown status counts as waivers.
export function displayTargets(g: ClaimGroup, status: Status): { id: number; now: boolean }[] {
  return g.targets.map((id) => ({ id, now: isFA(id, status) }));
}

// The group whose list holds him, or null.
export function holder(groups: ClaimGroup[], list: List, id: number): number | null {
  const gi = groups.findIndex((g) => g[list].includes(id));
  return gi < 0 ? null : gi;
}

// A drag's edit: take him out of the group he is dragged from and put him in group `gi` at `at`, a
// position in the group's list. Inside
// one group it is an array move. A target is in one group, so he leaves whichever holds him; a cut
// may stand in several, so only `from` loses him (and a group that already cuts him keeps one row).
// Emptied groups are kept: see prune.
export function place(groups: ClaimGroup[], list: List, id: number, gi: number, at: number, from?: number): ClaimGroup[] {
  const out = copy(groups).map((g, i) => (list === "cuts" && from != null && i !== from && i !== gi ? g : { ...g, [list]: g[list].filter((x) => x !== id) }));
  out[gi][list].splice(Math.max(0, Math.min(at, out[gi][list].length)), 0, id);
  return out;
}

// "FA" (added the moment you submit), "W·Wed" (on waivers, clears that day, local time), "W" with no
// date, "" when the status is unknown.
export function statusTag(p: { status: PoolStatus | null; waiver_date: number | null } | undefined): string {
  if (!p?.status) return "";
  if (p.status === "FREEAGENT") return "FA";
  return p.waiver_date ? `W·${new Date(p.waiver_date).toLocaleDateString("en-US", { weekday: "short" })}` : "W";
}

// Why a roster player cannot be a cut, or null. A locked one (his game started) can: no instant add
// drops him (ESPN blocks a free agent for him), so he goes for waiver claims only.
export function cutBlock(p: ClaimPlayer): string | null {
  if (p.slot === "IR") return "on IR";
  if (p.droppable === false) return "undroppable";
  return null;
}

// Where a plan error sits, for a list that mixes both: a group's, or the roster's (group null: an IR
// player with no injury designation, which blocks every add and claim).
export const errorWhere = (e: Pick<ClaimErr, "group">): string => (e.group == null ? "Roster" : `Group ${e.group + 1}`);

// The Submit dialog's claims verdict. `planned` is the claim count frozen when Send was pressed;
// `keptOutside` the claims filed on ESPN directly that were kept (they run ahead of ours). Never ok
// unless the server matched with no error and a read-back; a failed read-back says so, not "holds 0".
// `withdrawn`: an empty plan sent to cancel that many claims, so "no claims" reads as a withdrawal.
// `writes` false (a test server, FFL_WRITES=0): the fake held them, the text says "Test mode" first and never
// says ESPN holds anything.
export function submitSummary(
  r: Pick<SubmitResult, "sent" | "pending" | "match" | "error" | "readback_failed">, planned: number, keptOutside = 0,
  { withdrawn = 0, writes = true }: { withdrawn?: number; writes?: boolean } = {},
): { ok: boolean; text: string } {
  const v = verdict(r, planned, keptOutside, withdrawn, writes ? "ESPN" : "the fake", outsideWhere(writes));
  return writes ? v : { ...v, text: `Test mode: ${v.text}` };
}

// "filed on ESPN directly" / "filed outside the app": where a claim not sent from here came from.
export const outsideWhere = (writes: boolean) => (writes ? "filed on ESPN directly" : "filed outside the app");

function verdict(
  r: Pick<SubmitResult, "sent" | "pending" | "match" | "error" | "readback_failed">, planned: number, keptOutside: number,
  withdrawn: number, who: string, outside: string,
): { ok: boolean; text: string } {
  const n = r.sent.length;
  if (r.match && !r.error && !r.readback_failed) {
    if (!planned) {
      if (!withdrawn) return { ok: true, text: "No claims to send" };
      return keptOutside
        ? { ok: true, text: `Withdrawn ${withdrawn}: ${who} holds none of yours · ${keptOutside} ${outside} left as ${keptOutside === 1 ? "it was" : "they were"}` }
        : { ok: true, text: `Withdrawn ${withdrawn}: ${who} holds none of yours` };
    }
    return keptOutside
      ? { ok: true, text: `${who} holds all ${n} of yours, in this order · plus ${keptOutside} ${outside}, ahead of these` }
      : { ok: true, text: `${who} holds all ${n}, in this order` };
  }
  if (r.readback_failed) return { ok: false, text: withdrawn && !planned ? `Withdrawal sent, but couldn't read ${who} back` : `Sent ${n}, but couldn't read ${who} back` };
  const held = new Set(r.pending.map((p) => p.id));
  const k = r.sent.filter((c) => held.has(c.id)).length;
  if (!n) {
    if (withdrawn && !planned) return { ok: false, text: `The withdrawal didn't finish: see what ${who} still holds` };
    return { ok: false, text: planned ? `Nothing was sent: ${who} holds 0 of ${planned}` : "Nothing was sent" };
  }
  return { ok: false, text: k === n && n === planned ? `${who} holds all ${n}, in a different order` : `${who} holds ${k} of ${planned}` };
}

// How long the dialog waits for a submit. The server cancels every claim sent before, posts each
// instant add and claim, numbers the list and reads it back, one ESPN call each in turn (each allowed
// espn_write.TIMEOUT, 15 s, but a few seconds as a rule), then rebuilds the roster pages inline after an
// instant add: the wait grows with the plan, never under a minute, SUBMIT_PER_WRITE_MS a call plus the
// rebuild's margin, and never past SUBMIT_CEILING_MS, which next.config.ts's proxyTimeout matches (Next
// cuts a rewrite at 30 s by default).
export const SUBMIT_FLOOR_MS = 60_000;
export const SUBMIT_PER_WRITE_MS = 5_000;
export const SUBMIT_MARGIN_MS = 30_000;
export const SUBMIT_CEILING_MS = 300_000;
export function submitTimeoutMs(cancels: number, posts: number): number {
  const calls = cancels + posts + 2; // the reorder and the read-back
  return Math.min(SUBMIT_CEILING_MS, Math.max(SUBMIT_FLOOR_MS, calls * SUBMIT_PER_WRITE_MS + SUBMIT_MARGIN_MS));
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

// GET /claims/submit/{token}: the send still running, or what its POST answered
export type SubmitAnswer = { state: "running" } | { state: "done"; status: number; body: unknown };
export const RECOVER_POLL_MS = 3_000;

// A send whose request dropped with no answer ("Load failed": the phone left the app mid-send; or the dialog's own
// timeout) while the server, which never noticed, may still be sending. Ask for its answer by the send's token until
// it is in; a failed ask (still offline) asks again; a 404 means the server never saw the send. Up to
// SUBMIT_CEILING_MS, the longest a send may run. `get` throws "<status> <text>" on a refusal, as clientGet does.
export async function recoverSubmit(
  get: (path: string) => Promise<SubmitAnswer>, token: string, lost: string,
  { pollMs = RECOVER_POLL_MS, ceilingMs = SUBMIT_CEILING_MS }: { pollMs?: number; ceilingMs?: number } = {},
): Promise<{ result: SubmitResult } | { failure: SubmitFailure }> {
  const until = Date.now() + ceilingMs;
  for (;;) {
    let a: SubmitAnswer | null = null;
    try {
      a = await get(`/claims/submit/${encodeURIComponent(token)}`);
    } catch (e) {
      if (/^404\b/.test(e instanceof Error ? e.message : String(e)))
        return { failure: { kind: "other", error: `${lost}, and the server has no record of this send: check the claims panel` } };
    }
    if (a?.state === "done") {
      if (a.status === 200) return { result: a.body as SubmitResult };
      const detail = (a.body as { detail?: unknown } | null)?.detail;
      return { failure: submitFailure(a.status, a.body, typeof detail === "string" ? detail : `${a.status}`) };
    }
    if (Date.now() >= until) return { failure: { kind: "other", error: `${lost}, and the server's answer never came: check the claims panel` } };
    await new Promise((r) => setTimeout(r, pollMs));
  }
}
