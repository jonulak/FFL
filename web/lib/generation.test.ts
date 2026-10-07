import { describe, expect, it } from "vitest";
import { generation } from "./generation";

describe("generation", () => {
  it("a GET started before a replace is stale when it lands after it", () => {
    const g = generation();
    const get = g.ticket();   // live refetch starts
    g.bump();                 // the POST answers: replace(next)
    expect(g.fresh(get)).toBe(false);  // the refetch resolves: dropped
  });
  it("a GET started after the replace lands", () => {
    const g = generation();
    g.bump();
    expect(g.fresh(g.ticket())).toBe(true);
  });
  it("with no replace in between every answer lands", () => {
    const g = generation();
    const a = g.ticket();
    expect(g.fresh(a)).toBe(true);
  });
});
