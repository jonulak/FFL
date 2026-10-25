import type { Player } from "./api";
import { injuryTone } from "./format";

// The broadcast board's shared vocabulary: the Matchup board and the team page set a player the same way.

// ESPN names the flex slot by what it takes; the board calls it FLEX (season.py SLOT_LABEL).
export const SLOT_LABEL: Record<string, string> = { "RB/WR/TE": "FLEX", "RB/WR": "FLEX", "WR/TE": "FLEX" };
// ESPN says ACTIVE for a player and NORMAL for a defence; neither is news.
export const HEALTHY = new Set(["ACTIVE", "NORMAL"]);

// A player's injury status is ESPN's own one-letter mark beside the name, in the status colour
// with no chip behind it: amber for a game-time call, red for out.
export const TONE = { ok: "text-muted-foreground", warn: "text-close-foreground dark:text-close", bad: "text-out" } as const;
export const STATUS_SHORT: Record<string, string> = { QUESTIONABLE: "Q", DOUBTFUL: "D", OUT: "O", "INJURY RESERVE": "IR", SUSPENSION: "S", "DAY TO DAY": "DTD", DAY_TO_DAY: "DTD" };

export const BOARD_CARD = "rounded-lg py-0 shadow-[0_10px_28px_-16px_rgb(0_0_0_/_0.55)] [--card-spacing:--spacing(3)] sm:[--card-spacing:--spacing(4)]";
export const LABEL = "font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground";

// Idle for the week: on bye, or OUT/IR/SUSP. His game cannot add points.
export const isIdle = (p: Pick<Player, "bye" | "injury">) => p.bye || injuryTone(p.injury) === "bad";
