import type { Field } from "./api";

// A unit on the field is shown by colour alone (orange on the field, red in the red zone, the
// half's wash in globals.css); `sr` is what a screen reader hears instead. "On field" is read off
// possession (the feed names no personnel): the offense of the club with the ball, the D/ST of
// the club without it. The red zone is the ball inside the 20, so a D/ST shows it defending its own.
export const FIELD: Record<Field, { sr: string }> = {
  on: { sr: "on the field" },
  red_zone: { sr: "on the field, in the red zone" },
};

// The overlay sets `field` only while the game is on; a payload cached past the final must not keep it.
export function fieldOf(p: { field?: Field | null; game?: { status: string } }): Field | null {
  return p.field && p.game?.status === "in" ? p.field : null;
}
