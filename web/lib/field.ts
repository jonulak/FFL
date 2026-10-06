import type { Field } from "./api";

// Where a player stands while his game is on, shown by colour alone as the half's wash in
// globals.css: an ink wash in the game but off the field (his club's defense is out, or the
// half), orange on the field, red in the red zone; `sr` is what a screen reader hears instead.
// "On field" is read off possession (the feed names no personnel): the offense of the club with
// the ball, the D/ST of the club without it. The red zone is the ball inside the 20, so a D/ST
// shows it defending its own.
export type InGame = Field | "off";

export const FIELD: Record<InGame, { sr: string }> = {
  off: { sr: "in a game, off the field" },
  on: { sr: "on the field" },
  red_zone: { sr: "on the field, in the red zone" },
};

// The overlay sets `field` only while the game is on; a payload cached past the final must not keep it.
export function fieldOf(p: { field?: Field | null; game?: { status: string } }): Field | null {
  return p.field && p.game?.status === "in" ? p.field : null;
}

// Every player whose game is in progress has a state: on the field (or in the red zone) when the
// overlay says so, otherwise off it. Nothing before kickoff or after the final.
export function inGameOf(p: { field?: Field | null; game?: { status: string } }): InGame | null {
  return p.game?.status === "in" ? fieldOf(p) ?? "off" : null;
}
