import { describe, expect, it } from "vitest";
import type { Card } from "./api";
import { accepts, afterFailure, applyMoves, fillOptions, gainText, irEligible, irIllegal, isMismatch, lineupFailure, movesFor, slotLabel, swapOptions, targets } from "./lineup";

const c = (player_id: number, pos: string, slot: string, play_proj: number, extra: Partial<Card> = {}) =>
  ({ player_id, name: `P${player_id}`, pos, slot, play_proj, locked: false,
     group: slot === "IR" ? "ir" : slot === "BE" ? "bench" : "starter", call: "START", injury: "ACTIVE", ...extra }) as Card;

const qb = c(1, "QB", "QB", 18), flex = c(2, "WR", "RB/WR/TE", 9), rb = c(3, "RB", "RB", 12),
  benchRb = c(4, "RB", "BE", 11), benchQb = c(5, "QB", "BE", 0), lockedWr = c(6, "WR", "WR", 14, { locked: true }),
  ir = c(7, "WR", "IR", 0, { injury: "INJURY_RESERVE" });
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
  it("a sat man ESPN still starts, not one on the bench", () => {
    expect(isMismatch({ group: "starter", call: "SIT" })).toBe(true);
    expect(isMismatch({ group: "bench", call: "SIT" })).toBe(false);
  });
  it("a starter in another slot than the call's (STRATEGY.md, Slot by kickoff)", () => {
    expect(isMismatch({ group: "starter", call: "START", slot: "RB", call_slot: "RB/WR/TE" })).toBe(true);
    expect(isMismatch({ group: "starter", call: "START", slot: "RB/WR/TE", call_slot: "RB/WR/TE" })).toBe(false);
  });
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
  it("nothing for a locked player", () => expect(targets(all, lockedWr, [])).toEqual([]));
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
      .toEqual({ text: "Moved in ESPN — reload to confirm", reload: true, moved: true }));
  it("an ESPN error names ESPN once", () => {
    expect(lineupFailure(502, { detail: { source: "espn", error: "ESPN login expired, refresh .env" } }, "x").text)
      .toBe("ESPN login expired, refresh .env");
    expect(lineupFailure(502, { detail: { source: "espn", error: "HTTP 500" } }, "x").text).toBe("ESPN: HTTP 500");
  });
  it("anything else the message", () => expect(lineupFailure(null, null, "timed out").text).toBe("Not moved: timed out"));
});

describe("swapOptions", () => {
  const key = (o: ReturnType<typeof swapOptions>[number]) =>
    o.kind === "swap" || o.kind === "locked" ? `${o.kind}:${o.with.player_id}` : o.kind === "slot" ? `slot:${o.slot}` : "bench";
  it("sorts by gain, best first", () =>
    expect(swapOptions(all, benchRb, []).map(key)).toEqual(["swap:2", "swap:3"]));  // flex +2, RB −1
  it("a tie goes to ESPN's slot order, then the name", () => {
    // a bench TE at 9 swaps ±0 with the flex TE and the TE: TE comes before FLEX in ESPN's order
    const te = c(10, "TE", "TE", 9), flexTe = c(11, "TE", "RB/WR/TE", 9), benchTe = c(12, "TE", "BE", 9);
    expect(swapOptions([flexTe, te, benchTe], benchTe, []).map(key)).toEqual(["swap:10", "swap:11"]);
    // the flex WR's ±0 swaps with two bench WRs at his projection read by name
    const b = c(8, "WR", "BE", 9, { name: "Brown" }), a = c(9, "WR", "BE", 9, { name: "Adams" });
    expect(swapOptions([flex, b, a], flex, []).map(key)).toEqual(["swap:9", "swap:8", "bench"]);
  });
  it("a starter can go to the bench, and an empty slot is an option", () => {
    const ks = swapOptions(all, rb, ["RB/WR/TE"]).map(key);
    expect(ks).toContain("slot:RB/WR/TE");
    expect(ks.at(-1)).toBe("bench");  // −12, the worst change
  });
  it("a locked partner is listed last, as locked", () => {
    const benchWr = c(13, "WR", "BE", 10);
    expect(swapOptions([...all, benchWr], benchWr, []).map(key)).toEqual(["swap:2", "locked:6"]);
  });
  it("nothing for a locked player", () => expect(swapOptions(all, lockedWr, [])).toEqual([]));
});

describe("applyMoves", () => {
  const roster = (empty: string[] = []) => ({ week: 3, cards: all, empty_slots: empty });
  const slots = (r: { cards: Card[] }) => Object.fromEntries(r.cards.map((x) => [x.player_id, [x.slot, x.group]]));
  it("a swap trades the two slots and the groups follow", () => {
    const r = applyMoves(roster(), movesFor(benchRb, { kind: "swap", with: flex, gain: 2 }));
    expect(slots(r)[4]).toEqual(["RB/WR/TE", "starter"]);
    expect(slots(r)[2]).toEqual(["BE", "bench"]);
    expect(r.empty_slots).toEqual([]);
    expect(r.week).toBe(3);
  });
  it("to the bench leaves the slot empty", () => {
    const r = applyMoves(roster(), [{ player_id: 3, to_slot: "BE" }]);
    expect(slots(r)[3]).toEqual(["BE", "bench"]);
    expect(r.empty_slots).toEqual(["RB"]);
  });
  it("filling an empty slot removes it, in ESPN's slot order", () => {
    const r = applyMoves(roster(["RB", "RB/WR/TE"]), [{ player_id: 4, to_slot: "RB/WR/TE" }]);
    expect(slots(r)[4]).toEqual(["RB/WR/TE", "starter"]);
    expect(r.empty_slots).toEqual(["RB"]);
  });
  it("a starter moved into an empty slot leaves his own empty", () => {
    const r = applyMoves(roster(["RB/WR/TE"]), [{ player_id: 3, to_slot: "RB/WR/TE" }]);
    expect(r.empty_slots).toEqual(["RB"]);
  });
  it("orders the rows as ESPN's read-back will (season._card_sort_key), so the move does not play out twice", () => {
    // two FLEX starters and a bench WR ahead of both in ESPN's entries: swapped into the second FLEX, he reads
    // first, as ESPN lists him
    const f1 = c(10, "WR", "RB/WR/TE", 9, { proj: 9, name: "Flex A", espn_order: 3 }), f2 = c(11, "WR", "RB/WR/TE", 8, { proj: 8, name: "Flex B", espn_order: 4 });
    const te = c(12, "TE", "BE", 6, { proj: 6, name: "Tight", espn_order: 1 }), wr = c(13, "WR", "BE", 12, { proj: 12, name: "Wide", espn_order: 0 });
    const r = applyMoves({ cards: [f1, f2, te, wr], empty_slots: [] }, movesFor(wr, { kind: "swap", with: f2, gain: 4 }));
    // starters by slot then ESPN's order; the bench by position (WR before TE) then projection
    expect(r.cards.map((x) => x.player_id)).toEqual([13, 10, 11, 12]);
  });
  it("keeps ESPN's order inside a starting slot over the projection", () => {
    const hi = c(20, "QB", "QB", 18, { proj: 18, espn_order: 5 }), lo = c(21, "QB", "QB", 15, { proj: 15, espn_order: 2 });
    const bench = c(22, "WR", "BE", 3, { proj: 3, espn_order: 0 });
    expect(applyMoves({ cards: [hi, lo, bench], empty_slots: [] }, []).cards.map((x) => x.player_id)).toEqual([21, 20, 22]);
  });
  it("leaves the cards it does not move as they were", () => {
    const r = applyMoves(roster(), [{ player_id: 3, to_slot: "BE" }]);
    expect(r.cards.find((x) => x.player_id === 1)).toBe(qb);
  });
});

describe("afterFailure", () => {
  const before = { cards: all, empty_slots: [] as string[] };
  const optimistic = applyMoves(before, [{ player_id: 3, to_slot: "BE" }]);
  it("a move ESPN probably took keeps the optimistic rows", () =>
    expect(afterFailure(lineupFailure(502, { detail: { source: "espn", error: "x", moved: true } }, "x"), before, optimistic)).toBe(optimistic));
  it("every other failure puts the page back", () => {
    expect(afterFailure(lineupFailure(409, { detail: { errors: ["A is locked"] } }, "x"), before, optimistic)).toBe(before);
    expect(afterFailure(lineupFailure(502, { detail: { source: "espn", error: "HTTP 500" } }, "x"), before, optimistic)).toBe(before);
    expect(afterFailure(lineupFailure(null, null, "timed out"), before, optimistic)).toBe(before);
  });
});

describe("fillOptions", () => {
  const key = (o: ReturnType<typeof fillOptions>[number]) => (o.kind === "fill" ? `fill:${o.who.player_id}:${o.gain}` : `locked:${o.with.player_id}`);
  it("bench players add their projection, starters elsewhere move for ±0, best first; locked last", () =>
    // FLEX takes RB/WR/TE: bench RB 11, the RB starter (0); the flex WR is already there; the locked WR listed shut;
    // the bench QB does not fit; the IR WR comes off IR at his projection (0, he is hurt)
    expect(fillOptions(all.filter((x) => x.player_id !== 2), "RB/WR/TE").map(key)).toEqual(["fill:4:11", "fill:3:0", "fill:7:0", "locked:6"]));
  it("ties read by name", () => {
    const b = c(8, "WR", "BE", 9, { name: "Brown" }), a = c(9, "WR", "BE", 9, { name: "Adams" });
    expect(fillOptions([b, a], "WR").map(key)).toEqual(["fill:9:9", "fill:8:9"]);
  });
  it("OP takes a QB from the bench", () => expect(fillOptions(all, "OP").map(key)).toContain("fill:5:0"));
  it("nobody already in a slot of that type", () =>
    expect(fillOptions(all, "RB").map(key)).toEqual(["fill:4:11"]));
});

describe("IR", () => {
  const out = c(20, "RB", "BE", 0, { injury: "OUT" }), irStarter = c(21, "WR", "WR", 7, { injury: "INJURY_RESERVE" }),
    q = c(22, "WR", "BE", 6, { injury: "QUESTIONABLE" }), benchWr = c(23, "WR", "BE", 8);
  const roster = [qb, flex, rb, benchRb, benchQb, lockedWr, ir, out, irStarter, q, benchWr];
  const key = (o: ReturnType<typeof swapOptions>[number]) =>
    o.kind === "swap" ? `swap:${o.with.player_id}:${o.gain}` : o.kind === "locked" ? `locked:${o.with.player_id}`
      : o.kind === "slot" ? `slot:${o.slot}:${o.gain}` : `bench:${o.gain}`;
  const fkey = (o: ReturnType<typeof fillOptions>[number]) => (o.kind === "fill" ? `fill:${o.who.player_id}:${o.gain}` : `locked:${o.with.player_id}`);

  it("only IR or Out may go in", () => {
    expect(["INJURY_RESERVE", "OUT"].map((injury) => irEligible({ injury }))).toEqual([true, true]);
    expect(["ACTIVE", "QUESTIONABLE", "DOUBTFUL", "SUSPENSION", "NORMAL", ""].some((injury) => irEligible({ injury }))).toBe(false);
  });
  it("an IR player goes to the bench, an empty starting slot that takes him, or swaps with an IR-eligible player", () => {
    // the flex WR (ACTIVE), the bench WR and the Questionable WR cannot take IR; the Out RB and the IR-listed WR starter can
    expect(swapOptions(roster, ir, ["RB/WR/TE"]).map(key)).toEqual(["slot:RB/WR/TE:0", "bench:0", "swap:20:0", "swap:21:-7"]);
  });
  it("an IR-eligible player gets To IR when IR has room, and the swap with the occupant", () => {
    expect(swapOptions(roster.filter((x) => x !== ir), out, ["IR"]).map(key)).toContain("slot:IR:0");
    expect(swapOptions(roster, out, []).map(key)).toContain("swap:7:0");
    // a starter listed IR takes his projection out of the lineup when he goes in
    expect(swapOptions(roster.filter((x) => x !== ir), irStarter, ["IR"]).map(key)).toContain("slot:IR:-7");
  });
  it("an ineligible player is never offered IR", () => {
    for (const p of [benchRb, q, benchWr, flex]) {
      expect(swapOptions(roster.filter((x) => x !== ir), p, ["IR"]).map(key).some((k) => k.startsWith("slot:IR"))).toBe(false);
      expect(swapOptions(roster, p, []).map(key).some((k) => k.startsWith("swap:7:"))).toBe(false);
    }
  });
  it("an empty IR's fill lists only players listed IR or Out", () =>
    expect(fillOptions(roster.filter((x) => x !== ir), "IR").map(fkey)).toEqual(["fill:20:0", "fill:21:-7"]));
  it("moves into IR re-slot to the ir group and fill the empty IR", () => {
    const r = applyMoves({ cards: roster.filter((x) => x !== ir), empty_slots: ["IR"] }, [{ player_id: 20, to_slot: "IR" }]);
    expect(r.cards.find((x) => x.player_id === 20)).toMatchObject({ slot: "IR", group: "ir" });
    expect(r.empty_slots).toEqual([]);
  });
  it("moving the IR player off leaves IR empty, after the starting slots", () => {
    const r = applyMoves({ cards: [rb, ir], empty_slots: [] }, [{ player_id: 7, to_slot: "BE" }, { player_id: 3, to_slot: "BE" }]);
    expect(r.cards.find((x) => x.player_id === 7)).toMatchObject({ slot: "BE", group: "bench" });
    expect(r.empty_slots).toEqual(["RB", "IR"]);
  });
});

describe("illegal IR", () => {
  const healed = c(7, "WR", "IR", 0, { injury: "ACTIVE" }), out = c(20, "RB", "BE", 0, { injury: "OUT" });
  const roster = [qb, flex, rb, benchRb, benchQb, healed, out];
  it("is an IR player with no designation; Q, D, Out, IR and suspension may stay", () => {
    expect(irIllegal(roster)).toBe(healed);
    for (const injury of ["ACTIVE", "NORMAL", ""]) expect(irIllegal([c(7, "WR", "IR", 0, { injury })])?.player_id).toBe(7);
    expect(irIllegal([c(7, "WR", "IR", 0, { injury: null as unknown as string })])?.player_id).toBe(7);
    for (const injury of ["QUESTIONABLE", "DOUBTFUL", "OUT", "INJURY_RESERVE", "SUSPENSION"]) {
      expect(irIllegal([c(7, "WR", "IR", 0, { injury })])).toBeNull();
    }
    expect(irIllegal([c(8, "WR", "BE", 0)])).toBeNull();
  });
  it("blocks every other player's moves but the one that takes his place on IR", () => {
    expect(swapOptions(roster, benchRb, ["RB/WR/TE"])).toEqual([]);
    expect(swapOptions(roster, flex, [])).toEqual([]);
    expect(swapOptions(roster, out, []).map((o) => (o.kind === "swap" ? o.with.player_id : o.kind))).toEqual([7]);
  });
  it("leaves his own sheet as usual", () =>
    expect(swapOptions(roster, healed, ["RB/WR/TE"]).map((o) => (o.kind === "swap" ? o.with.player_id : o.kind))).toEqual(["slot", "bench", 20]));
  it("lets an empty slot be filled only by him", () => {
    expect(fillOptions(roster, "RB/WR/TE").map((o) => (o.kind === "fill" ? o.who.player_id : o.kind))).toEqual([7]);
    expect(fillOptions(roster, "QB")).toEqual([]);
  });
});
