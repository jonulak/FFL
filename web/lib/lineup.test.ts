import { describe, expect, it } from "vitest";
import type { Card } from "./api";
import { accepts, gainText, isMismatch, lineupFailure, movesFor, slotLabel, targets } from "./lineup";

const c = (player_id: number, pos: string, slot: string, play_proj: number, extra: Partial<Card> = {}) =>
  ({ player_id, name: `P${player_id}`, pos, slot, play_proj, locked: false,
     group: slot === "IR" ? "ir" : slot === "BE" ? "bench" : "starter", call: "START", ...extra }) as Card;

const qb = c(1, "QB", "QB", 18), flex = c(2, "WR", "RB/WR/TE", 9), rb = c(3, "RB", "RB", 12),
  benchRb = c(4, "RB", "BE", 11), benchQb = c(5, "QB", "BE", 0), lockedWr = c(6, "WR", "WR", 14, { locked: true }),
  ir = c(7, "WR", "IR", 0);
const all = [qb, flex, rb, benchRb, benchQb, lockedWr, ir];

describe("slots", () => {
  it("flex takes RB/WR/TE, not QB", () => expect([...accepts("RB/WR/TE")].sort()).toEqual(["RB", "TE", "WR"]));
  it("OP takes a QB", () => expect(accepts("OP").has("QB")).toBe(true));
  it("labels", () => { expect(slotLabel("RB/WR/TE")).toBe("FLEX"); expect(slotLabel("BE")).toBe("Bench"); });
});

describe("mismatch", () => {
  it("a starter the call benches", () => expect(isMismatch({ group: "starter", call: "BENCH" })).toBe(true));
  it("a bench player the call starts", () => expect(isMismatch({ group: "bench", call: "START" })).toBe(true));
  it("agreement", () => expect(isMismatch({ group: "starter", call: "START" })).toBe(false));
  it("OUT and BYE on the bench are not a mismatch", () => expect(isMismatch({ group: "bench", call: "OUT" })).toBe(false));
});

describe("targets", () => {
  it("a bench RB can take the flex or the RB slot, with the lineup's change", () => {
    const t = targets(all, benchRb, []);
    expect(t).toEqual([
      { kind: "swap", with: flex, gain: 2 },  // 11 in, 9 out
      { kind: "swap", with: rb, gain: -1 },
    ]);
  });
  it("never offers a locked player, IR, or a swap where either cannot take the other's slot", () => {
    // flex WR: the bench RB (gain 11 - 9) and the bench; not the QB or RB slots (a WR fits neither),
    // not the bench QB (no FLEX for a QB), not the locked WR, not IR
    const ids = targets(all, flex, []).map((t) => (t.kind === "swap" ? t.with.player_id : t.kind));
    expect(ids).toEqual([4, "bench"]);
  });
  it("a starter can go to the bench on its own, and an empty slot is offered", () => {
    const t = targets(all, rb, ["RB/WR/TE"]);
    expect(t).toContainEqual({ kind: "bench", gain: -12 });
    expect(t).toContainEqual({ kind: "slot", slot: "RB/WR/TE", gain: 0 });
  });
  it("from the bench into an empty slot adds his projection", () =>
    expect(targets(all, benchRb, ["RB/WR/TE"])).toContainEqual({ kind: "slot", slot: "RB/WR/TE", gain: 11 }));
  it("nothing for a locked or IR player", () => {
    expect(targets(all, lockedWr, [])).toEqual([]); expect(targets(all, ir, [])).toEqual([]);
  });
  it("skips an empty slot equal to the starter's current slot (no-op)", () => {
    // RB starter in "RB" with empty ["RB"] should not offer a no-op "move to RB" target
    const t = targets(all, rb, ["RB"]);
    expect(t).not.toContainEqual({ kind: "slot", slot: "RB", gain: 0 });
    // But a bench RB with empty ["RB"] should offer it (he's not in the RB slot yet)
    expect(targets(all, benchRb, ["RB"])).toContainEqual({ kind: "slot", slot: "RB", gain: 11 });
  });
});

describe("movesFor", () => {
  it("a swap moves both", () => expect(movesFor(benchRb, { kind: "swap", with: flex, gain: 2 })).toEqual([
    { player_id: 4, to_slot: "RB/WR/TE" }, { player_id: 2, to_slot: "BE" }]));
  it("to the bench", () => expect(movesFor(rb, { kind: "bench", gain: -12 })).toEqual([{ player_id: 3, to_slot: "BE" }]));
  it("into an empty slot", () => expect(movesFor(benchRb, { kind: "slot", slot: "RB", gain: 11 })).toEqual([{ player_id: 4, to_slot: "RB" }]));
});

describe("gainText", () => {
  it("signs a gain, a true minus for a loss", () => { expect(gainText(2)).toBe("+2.0"); expect(gainText(-1)).toBe("−1.0"); });
  it("zero and minus zero are ±0", () => { expect(gainText(0)).toBe("±0"); expect(gainText(-0)).toBe("±0"); expect(gainText(-0.04)).toBe("±0"); });
});

describe("lineupFailure", () => {
  it("refused moves join their reasons", () =>
    expect(lineupFailure(409, { detail: { errors: ["A is locked", "B can't play FLEX"] } }, "x"))
      .toEqual({ text: "A is locked; B can't play FLEX", reload: false, moved: false }));
  it("a week change offers a reload", () =>
    expect(lineupFailure(409, { detail: { error: "ESPN's week changed — reload" } }, "x"))
      .toEqual({ text: "ESPN's week changed — reload", reload: true, moved: false }));
  it("a move already running says so", () =>
    expect(lineupFailure(409, { detail: "a lineup move is already running" }, "x").text).toBe("a lineup move is already running"));
  it("a move ESPN took but could not read back is never a failure", () =>
    expect(lineupFailure(502, { detail: { source: "espn", error: "moved in ESPN — the read-back failed, reload", moved: true } }, "x"))
      .toEqual({ text: "Moved in ESPN — reload to see it", reload: true, moved: true }));
  it("an ESPN error names ESPN", () =>
    expect(lineupFailure(502, { detail: { source: "espn", error: "ESPN login expired, refresh .env" } }, "x").text)
      .toBe("ESPN: ESPN login expired, refresh .env"));
  it("anything else the message", () => expect(lineupFailure(null, null, "timed out").text).toBe("Not moved: timed out"));
});
