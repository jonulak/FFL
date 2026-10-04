import type { Field } from "./api";

// What the board says about a unit on the field. "On field" is read off possession (the feed
// names no personnel): the offense of the club with the ball, the D/ST of the club without it.
// The red zone is the ball inside the 20, so a D/ST shows it while defending its own.
export const FIELD: Record<Field, { label: string; chip: string | null; sr: string }> = {
  on: { label: "on field", chip: null, sr: "on the field" },
  red_zone: { label: "red zone", chip: "RZ", sr: "on the field, in the red zone" },
};

// The overlay sets `field` only while the game is on; a payload cached past the final must not keep it.
export function fieldOf(p: { field?: Field | null; game?: { status: string } }): Field | null {
  return p.field && p.game?.status === "in" ? p.field : null;
}
