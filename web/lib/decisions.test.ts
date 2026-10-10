import { describe, expect, it } from "vitest";
import { askFor, byWeek, formError, needsRationale, reasonText, savedNote } from "./decisions";
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
  it("asks for a reason only when the move was not the call", () => {
    expect(askFor({ id: 1, needs_rationale: true })).toBe("dialog");
    expect(askFor({ id: 1, needs_rationale: false })).toBe("toast");
    expect(askFor(null)).toBeNull();
    expect(askFor(undefined)).toBeNull();
  });
  it("says when a free-add copy of the reason failed", () => {
    expect(savedNote(0)).toBe("Saved");
    expect(savedNote(1)).toBe("Saved. 1 add still needs a reason (Decisions page)");
    expect(savedNote(2)).toBe("Saved. 2 adds still need a reason (Decisions page)");
  });
  it("groups newest week first, weekless last", () => {
    expect(byWeek([d({ id: 1, week: 5 }), d({ id: 2, week: null }), d({ id: 3, week: 6 })]).map((g) => g.week)).toEqual([6, 5, null]);
  });
});
