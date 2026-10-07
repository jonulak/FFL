import { describe, expect, it } from "vitest";
import {
  addTarget, atMost, capacity, cutBlock, displayTargets, groupHasFA, groupPos, move, moveGroup, removeFrom, reorderTargets,
  addCut, holder, place, processDay, prune, rowTag, setOpen, statusTag, submitFailure, submitSummary,
} from "./claims";
import type { ClaimGroup, ClaimPlayer } from "./api";

const pos = new Map([[101, "WR"], [102, "WR"], [103, "WR"], [201, "TE"]]);
const G: ClaimGroup[] = [{ targets: [101, 102, 103], cuts: [1, 2], open: 0 }, { targets: [201], cuts: [3], open: 0 }];

describe("claim groups", () => {
  it("capacity counts cuts and open spots", () => expect(capacity({ targets: [1], cuts: [2], open: 1 })).toBe(2));
  it("row tag is position · rank", () => {
    expect(rowTag(102, G, pos)).toBe("WR·2");
    expect(rowTag(999, G, pos)).toBeNull();
  });
  it("mixed group", () => expect(groupPos({ targets: [101, 201], cuts: [], open: 0 }, pos)).toBe("mixed"));
  it("empty group has no position", () => expect(groupPos({ targets: [], cuts: [1], open: 0 }, pos)).toBe(""));
  it("adds to a new group", () => expect(addTarget(G, "new", 104).at(-1)).toEqual({ targets: [104], cuts: [], open: 0 }));
  it("adding a target already in another group moves it", () => {
    const g = addTarget(G, 1, 101);
    expect(g[0].targets).toEqual([102, 103]);
    expect(g[1].targets).toEqual([201, 101]);
  });
  it("adding to the group he is already in keeps one copy", () => expect(addTarget(G, 0, 101)[0].targets).toEqual([102, 103, 101]));
  it("moving a group's only target out drops the emptied group only when it has no cuts", () => {
    const g = addTarget([{ targets: [7], cuts: [], open: 0 }, { targets: [8], cuts: [], open: 0 }], 1, 7);
    expect(g).toEqual([{ targets: [8, 7], cuts: [], open: 0 }]);
  });
  it("does not mutate its input", () => {
    const before = JSON.stringify(G);
    addTarget(G, 1, 101); removeFrom(G, 0, "cuts", 1); move(G, 0, "targets", 0, 2); setOpen(G, 0, true, 2); moveGroup(G, 0, 1);
    expect(JSON.stringify(G)).toBe(before);
  });
  it("removing the last target and cut drops the group", () => {
    const one = [{ targets: [201], cuts: [], open: 0 }];
    expect(removeFrom(one, 0, "targets", 201)).toEqual([]);
  });
  it("removing a cut keeps a group with targets", () => expect(removeFrom(G, 1, "cuts", 3)[1]).toEqual({ targets: [201], cuts: [], open: 0 }));
  it("move reorders within a list", () => expect(move(G, 0, "targets", 2, 0)[0].targets).toEqual([103, 101, 102]));
  it("moves groups", () => expect(moveGroup(G, 1, 0).map((g) => g.targets[0])).toEqual([201, 101]));
  it("open spots belong to one group", () => {
    const g = setOpen(setOpen(G, 0, true, 2), 1, true, 2);
    expect(g.map((x) => x.open)).toEqual([0, 2]);
    expect(setOpen(g, 1, false, 2).map((x) => x.open)).toEqual([0, 0]);
  });
  it("at most", () => expect(atMost(G, pos)).toBe("At most: 2 WR, 1 TE"));
  it("at most caps at the target count", () => expect(atMost([{ targets: [201], cuts: [1, 2], open: 0 }], pos)).toBe("At most: 1 TE"));
  it("at most names a mixed group", () =>
    expect(atMost([{ targets: [101, 201], cuts: [1], open: 0 }], pos)).toBe("At most: 1 of WR/TE"));
});

describe("free agents", () => {
  it("free agents display first, flagged now", () =>
    expect(displayTargets(G[0], { "102": "FREEAGENT" })).toEqual([{ id: 102, now: true }, { id: 101, now: false }, { id: 103, now: false }]));
  it("unknown status counts as waivers", () => expect(displayTargets(G[0], {}).every((t) => !t.now)).toBe(true));
  it("group has FA", () => {
    expect(groupHasFA(G[0], { "103": "FREEAGENT" })).toBe(true);
    expect(groupHasFA(G[0], { "103": "WAIVERS" })).toBe(false);
  });
  it("reorder within the waiver block keeps the FA block first", () => {
    const g = { targets: [101, 102, 103], cuts: [], open: 0 };
    // 102 is FA: display is [102 | 101, 103]; move 103 above 101 in the waiver block
    expect(reorderTargets(g, { "102": "FREEAGENT" }, false, 1, 0)).toEqual([102, 103, 101]);
    expect(reorderTargets({ targets: [101, 102, 103], cuts: [], open: 0 }, { "101": "FREEAGENT", "103": "FREEAGENT" }, true, 1, 0))
      .toEqual([103, 101, 102]);
  });
  it("status tag", () => {
    expect(statusTag({ status: "FREEAGENT", waiver_date: null })).toBe("FA");
    expect(statusTag({ status: "WAIVERS", waiver_date: new Date(2026, 9, 7, 3).getTime() })).toBe("W·Wed");
    expect(statusTag({ status: "WAIVERS", waiver_date: null })).toBe("W");
    expect(statusTag(undefined)).toBe("");
  });
  it("cut blocks", () => {
    const p: ClaimPlayer = { name: "x", pos: "WR", team: "DET", add: null, ros_adj: 50, slot: "BE", status: null, waiver_date: null, droppable: true, locked: true };
    expect(cutBlock(p, false)).toBeNull();
    expect(cutBlock(p, true)).toBe("game started");
    expect(cutBlock({ ...p, droppable: false }, false)).toBe("undroppable");
    expect(cutBlock({ ...p, slot: "IR" }, false)).toBe("on IR");
  });
});

describe("empty groups and the process day", () => {
  it("an empty group made from the panel survives edits to other groups", () => {
    const g = [...G, { targets: [], cuts: [], open: 0 }];
    expect(addTarget(g, 1, 101)).toHaveLength(3);
    expect(removeFrom(g, 1, "cuts", 3)).toHaveLength(3);
    expect(addTarget(g, 2, 104)[2]).toEqual({ targets: [104], cuts: [], open: 0 });
  });
  it("adds a cut, moving it out of another group", () => {
    const g = addCut(G, 1, 1);
    expect(g[0].cuts).toEqual([2]);
    expect(g[1].cuts).toEqual([3, 1]);
  });
  it("process day is the earliest waiver date's weekday, Wed by default", () => {
    expect(processDay([null, new Date(2026, 9, 8, 3).getTime(), new Date(2026, 9, 7, 3).getTime()])).toBe("Wed");
    expect(processDay([new Date(2026, 9, 8, 3).getTime()])).toBe("Thu");
    expect(processDay([])).toBe("Wed");
  });
});

describe("dragging between groups", () => {
  it("finds the group that holds a player", () => {
    expect(holder(G, "targets", 201)).toBe(1);
    expect(holder(G, "cuts", 2)).toBe(0);
    expect(holder(G, "cuts", 201)).toBeNull();
  });
  it("places a target in another group at a position", () => {
    const g = place(G, {}, "targets", 102, 1, 0);
    expect(g[0].targets).toEqual([101, 103]);
    expect(g[1].targets).toEqual([102, 201]);
  });
  it("places a cut at the end of another group", () => {
    const g = place(G, {}, "cuts", 1, 1, 9);
    expect(g[0].cuts).toEqual([2]);
    expect(g[1].cuts).toEqual([3, 1]);
  });
  it("reorders inside one group like an array move", () => expect(place(G, {}, "targets", 101, 0, 2)[0].targets).toEqual([102, 103, 101]));
  it("a free agent lands in its group's FA block, wherever it is dropped", () => {
    const g = place(G, { "201": "FREEAGENT" }, "targets", 201, 0, 3);
    expect(g[0].targets).toEqual([201, 101, 102, 103]);
  });
  it("keeps a group the drag emptied until the drop prunes it", () => {
    const g = place(G, {}, "targets", 201, 0, 0);
    const moved = place(g, {}, "cuts", 3, 0, 0);
    expect(moved).toHaveLength(2);
    expect(prune(G, moved)).toEqual([{ targets: [201, 101, 102, 103], cuts: [3, 1, 2], open: 0 }]);
  });
  it("prune keeps a group that was already empty", () => {
    const g = [...G, { targets: [], cuts: [], open: 0 }];
    expect(prune(g, place(g, {}, "cuts", 3, 0, 0))).toHaveLength(3);
  });
  it("does not mutate its input", () => {
    const before = JSON.stringify(G);
    place(G, {}, "targets", 102, 1, 0);
    expect(JSON.stringify(G)).toBe(before);
  });
});

describe("submit summary", () => {
  const sent = [{ group: 0, add: 1, drop: 2, id: "a" }, { group: 0, add: 3, drop: 2, id: "b" }];
  const ok = { error: null, readback_failed: false };
  it("all held", () => expect(submitSummary({ ...ok, sent, pending: sent, match: true }, 2)).toEqual({ ok: true, text: "ESPN holds all 2, in this order" }));
  it("short", () => expect(submitSummary({ ...ok, sent, pending: sent.slice(0, 1), match: false }, 2)).toEqual({ ok: false, text: "ESPN holds 1 of 2" }));
  it("same set, wrong order", () =>
    expect(submitSummary({ ...ok, sent, pending: [sent[1], sent[0]], match: false }, 2)).toEqual({ ok: false, text: "ESPN holds all 2, in a different order" }));
  it("read-back failed is not zero", () =>
    expect(submitSummary({ ...ok, sent, pending: [], match: false, readback_failed: true }, 2)).toEqual({ ok: false, text: "Sent 2, but couldn't read ESPN back" }));
  it("nothing to send comes from the plan, not from sent", () =>
    expect(submitSummary({ ...ok, sent: [], pending: [], match: true }, 0)).toEqual({ ok: true, text: "No claims to send" }));
  it("a failed first write is never ok", () => {
    const s = submitSummary({ sent: [], pending: [], match: false, error: "x", readback_failed: false }, 0);
    expect(s.ok).toBe(false);
    expect(submitSummary({ sent: [], pending: [], match: false, error: "x", readback_failed: false }, 2))
      .toEqual({ ok: false, text: "Nothing was sent: ESPN holds 0 of 2" });
  });
  it("match with an error is not ok", () => expect(submitSummary({ ...ok, sent, pending: sent, match: true, error: "x" }, 2).ok).toBe(false));
  it("kept outside claims ahead of ours", () =>
    expect(submitSummary({ ...ok, sent, pending: [{ id: "o", add: 9, drop: 1 }, ...sent], match: true }, 2, 1)).toEqual({
      ok: true, text: "ESPN holds all 2 of yours, in this order · plus 1 filed on ESPN directly, ahead of these" }));
});

describe("submitFailure", () => {
  it("a 409 with a string detail is a submit already running", () =>
    expect(submitFailure(409, { detail: "a submit is already running" }, "x")).toEqual({ kind: "running" }));
  it("a 409 with an error object is a change since the review", () => {
    expect(submitFailure(409, { detail: { error: "statuses changed — review again", now: [] } }, "x"))
      .toEqual({ kind: "changed", error: "statuses changed — review again" });
    expect(submitFailure(409, { detail: { error: "ESPN's week changed — reload" } }, "x"))
      .toEqual({ kind: "changed", error: "ESPN's week changed — reload" });
  });
  it("422 reasons, 502 ESPN, anything else the message", () => {
    const errors = [{ group: 0, player_id: 5, reason: "already on your roster" }];
    expect(submitFailure(422, { detail: { errors } }, "x")).toEqual({ kind: "invalid", errors });
    expect(submitFailure(502, { detail: { source: "espn", error: "ESPN login expired" } }, "x")).toEqual({ kind: "espn", error: "ESPN login expired" });
    expect(submitFailure(null, null, "timed out")).toEqual({ kind: "other", error: "timed out" });
  });
});
