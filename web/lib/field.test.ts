import { describe, expect, it } from "vitest";
import { FIELD, fieldOf } from "./field";

describe("fieldOf", () => {
  it("is the overlay's mark while the game is on", () => {
    expect(fieldOf({ field: "on", game: { status: "in" } })).toBe("on");
    expect(fieldOf({ field: "red_zone", game: { status: "in" } })).toBe("red_zone");
  });
  it("is nothing off the field, before kickoff or after the final", () => {
    expect(fieldOf({ game: { status: "in" } })).toBeNull();
    expect(fieldOf({ field: "on" })).toBeNull();
    expect(fieldOf({ field: "red_zone", game: { status: "post" } })).toBeNull();
  });
  it("chips only the red zone", () => {
    expect(FIELD.on.chip).toBeNull();
    expect(FIELD.red_zone.chip).toBe("RZ");
  });
});
