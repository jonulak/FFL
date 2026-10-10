import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { byWeek, changedReason, deferred, followedLineup, followedSit, formError, needsRationale, reasonText, savedNote, whyRows } from "./decisions";
import type { Decision } from "./api";

const P = { lineup: [{ key: "followed_call", label: "Followed the call" }, { key: "matchup", label: "Matchup" }, { key: "other", label: "Other" }] };
const d = (o: Partial<Decision>) => ({ id: 1, at: "2026-10-10", week: 6, kind: "lineup", source: "app", title: "t", decision: "",
  facts: null, rationale: null, rationale_note: null, followed_call: null, alternatives: null, exit_conditions: null, outcome: null,
  outcome_at: null, body: null, player_ids: null, ...o }) as Decision;

describe("decisions", () => {
  it("reads the reason as labels plus the note", () => {
    expect(reasonText(d({ rationale: ["matchup"], rationale_note: "DAL run D" }), P)).toBe("Matchup — DAL run D");
    expect(reasonText(d({ rationale: ["other"], rationale_note: "gut" }), P)).toBe("gut");
    expect(reasonText(d({}), P)).toBeNull();
    expect(needsRationale(d({}))).toBe(true);
  });
  it("wants a note under Other", () => {
    expect(formError("other", "  ")).toBe("Say why under Other");
    expect(formError("matchup", "")).toBeNull();
  });
  it("refuses a Save with no reason unless an outcome is given", () => {
    expect(formError(null, "")).toBe("Pick a reason");
    expect(formError(null, "a note")).toBe("Pick a reason");
    expect(formError(null, "", "")).toBe("Pick a reason");
    expect(formError(null, "", "it worked")).toBeNull();
    expect(formError("matchup", "", "")).toBeNull();
  });
  it("tells a changed reason from the one stored", () => {
    const other = { rationale: ["other"], rationale_note: null };
    expect(changedReason(other, "other", "")).toBe(false);
    expect(changedReason(other, "other", "  ")).toBe(false);
    expect(changedReason(other, "other", "gut")).toBe(true);
    expect(changedReason(other, "matchup", "")).toBe(true);
    expect(changedReason({ rationale: ["matchup"], rationale_note: "DAL" }, "matchup", "DAL ")).toBe(false);
    expect(changedReason({ rationale: null, rationale_note: null }, null, "")).toBe(false);
    expect(changedReason({ rationale: null, rationale_note: null }, "matchup", "")).toBe(true);
    expect(changedReason({}, "matchup", "")).toBe(true);
  });
  it("asks the claims reason only of the rows that still need one", () => {
    const r = (id: number, needs_rationale: boolean) => ({ id, needs_rationale });
    expect(whyRows([r(1, true), r(2, true), r(3, false)])).toEqual({ ids: [1, 2], needed: true });
    expect(whyRows([r(1, false), r(2, true)])).toEqual({ ids: [2], needed: true });
    // nothing open (a resubmit whose rows already have a reason): the form leaves, a reason saved early lands on all
    expect(whyRows([r(1, false), r(2, false)])).toEqual({ ids: [1, 2], needed: false });
    expect(whyRows([])).toEqual({ ids: [], needed: false });
  });
  it("says when a free-add copy of the reason failed", () => {
    expect(savedNote(0)).toBe("Saved");
    expect(savedNote(1)).toBe("Saved. 1 add still needs a reason (Decisions page)");
    expect(savedNote(2)).toBe("Saved. 2 adds still need a reason (Decisions page)");
  });
  it("groups newest week first, weekless last", () => {
    expect(byWeek([d({ id: 1, week: 5 }), d({ id: 2, week: null }), d({ id: 3, week: 6 })]).map((g) => g.week)).toEqual([6, 5, null]);
  });
  // the server's rule (ffl/decisions.py), run over the fixture tests/test_decisions.py runs too
  it("knows at the tap whether a move followed the call", () => {
    const f = JSON.parse(readFileSync(new URL("../../tests/fixtures/followed_call.json", import.meta.url), "utf8")) as Fixture;
    const by = new Map(f.cards.map((c) => [c.player_id, c]));
    for (const c of f.lineup)
      expect(followedLineup(c.moves.map(([player_id, , to_slot]) => ({ player_id, to_slot })), f.cards), c.why).toBe(c.followed);
    for (const c of f.sit) expect(followedSit(by.get(c.player_id)!, c.sat), c.why).toBe(c.followed);
  });
  it("hands the row ids over when the answer lands", async () => {
    const ids = deferred<number[]>();
    setTimeout(() => ids.resolve([7]), 0);
    await expect(ids.promise).resolves.toEqual([7]);
    const failed = deferred<number[]>();
    failed.reject(new Error("refused"));
    await expect(failed.promise).rejects.toThrow("refused");
  });
});

type Fixture = {
  cards: { player_id: number; call: string; model_call: string }[];
  lineup: { moves: [number, string, string][]; followed: boolean; why: string }[];
  sit: { player_id: number; sat: boolean; followed: boolean; why: string }[];
};
