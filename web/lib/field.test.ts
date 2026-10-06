import { describe, expect, it } from "vitest";
import { FIELD, fieldOf, inGameOf } from "./field";

describe("inGameOf", () => {
  it("marks every player whose game is on, off the field unless the overlay puts him on it", () => {
    expect(inGameOf({ game: { status: "in" } })).toBe("off");
    expect(inGameOf({ field: "on", game: { status: "in" } })).toBe("on");
    expect(inGameOf({ field: "red_zone", game: { status: "in" } })).toBe("red_zone");
  });
  it("is nothing before kickoff or after the final", () => {
    expect(inGameOf({})).toBeNull();
    expect(inGameOf({ field: "on", game: { status: "post" } })).toBeNull();
  });
  it("says off the field to a screen reader too", () => {
    expect(FIELD.off.sr).toContain("off the field");
  });
});

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
  it("says the red zone to a screen reader, since the board shows it by colour alone", () => {
    expect(FIELD.on.sr).toBe("on the field");
    expect(FIELD.red_zone.sr).toContain("red zone");
  });
});
