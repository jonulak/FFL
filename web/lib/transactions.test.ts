import { describe, expect, it } from "vitest";
import type { PlayerTx, TxMove, TxPlayer } from "./api";
import { byWeek, filterMoves, historyDetail, pairs, stamp, weekLabel } from "./transactions";

const p = (id: number): TxPlayer => ({ player_id: id, name: `P${id}`, pos: "WR", team: "ATL" });
const side = (team_id: number, added: TxPlayer[] = [], dropped: TxPlayer[] = []) =>
  ({ team_id, team: `T${team_id}`, abbrev: "", logo: "", added, dropped });
const move = (id: string, kind: TxMove["kind"], week: number, sides = [side(1, [p(1)])]): TxMove =>
  ({ id, ms: 0, at: "2026-09-16T03:05", kind, week, sides });

describe("filterMoves", () => {
  const moves = [move("a", "waiver", 2), move("b", "trade", 2, [side(1), side(7)]), move("c", "drop", 1, [side(3)])];
  it("keeps every move under All, one kind otherwise", () => {
    expect(filterMoves(moves, "all", null).map((m) => m.id)).toEqual(["a", "b", "c"]);
    expect(filterMoves(moves, "trade", null).map((m) => m.id)).toEqual(["b"]);
  });
  it("narrows to my team on either side of a trade", () => {
    expect(filterMoves(moves, "all", 7).map((m) => m.id)).toEqual(["b"]);
  });
});

describe("byWeek", () => {
  it("groups consecutive moves by week, in the order given", () => {
    const got = byWeek([move("a", "waiver", 3), move("b", "waiver", 3), move("c", "drop", 2)]);
    expect(got.map((g) => [g.week, g.moves.length])).toEqual([[3, 2], [2, 1]]);
  });
});

describe("weekLabel", () => {
  it("names preseason and the current week", () => {
    expect(weekLabel(0, 5)).toBe("Preseason");
    expect(weekLabel(5, 5)).toBe("Week 5 · this week");
    expect(weekLabel(4, 5)).toBe("Week 4");
  });
});

describe("pairs", () => {
  it("lines an add up with its drop", () => {
    expect(pairs({ added: [p(1)], dropped: [p(2)] })).toEqual([{ added: p(1), dropped: p(2) }]);
  });
  it("leaves the missing half empty", () => {
    expect(pairs({ added: [], dropped: [p(2)] })).toEqual([{ added: null, dropped: p(2) }]);
    expect(pairs({ added: [p(1), p(3)], dropped: [p(2)] })[1]).toEqual({ added: p(3), dropped: null });
  });
});

describe("historyDetail", () => {
  const h = (kind: PlayerTx["kind"], withs: string[], from: PlayerTx["from"] = null): PlayerTx =>
    ({ id: "x", at: "2026-09-16T03:05", week: 2, kind, team_id: 1, team: "T1", abbrev: "", logo: "", with: withs, from });
  it("names the other half of the pair", () => {
    expect(historyDetail(h("waiver", ["Cam Little"]))).toBe("for Cam Little");
    expect(historyDetail(h("dropped", ["Jonah Coleman", "Evan McPherson"]))).toBe("for Jonah Coleman, Evan McPherson");
  });
  it("reads a trade from the partner", () => {
    expect(historyDetail(h("traded", ["DeVonta Smith"], { team_id: 2, team: "My Njigbas " }))).toBe("from My Njigbas for DeVonta Smith");
  });
  it("says nothing for a lone move", () => {
    expect(historyDetail(h("dropped", []))).toBeNull();
  });
});

describe("stamp", () => {
  it("reads the log's local time", () => {
    expect(stamp("2026-09-16T03:05")).toBe("Wed, Sep 16 · 3:05 AM");
  });
  it("hands back what it cannot parse", () => {
    expect(stamp("soon")).toBe("soon");
  });
});
