"use client";

import { useState } from "react";
import type { GameState } from "@/lib/api";

// Once the game is on, the clock and quarter stand where the kickoff time was; "Final" after.
export function gameClock(g: GameState): string {
  if (g.status === "post") return "Final";
  const q = g.quarter ?? 0;
  if (q === 2 && g.clock === "0:00") return "Half";
  const period = q > 4 ? (q === 5 ? "OT" : `${q - 4}OT`) : `Q${q}`;
  return g.clock ? `${period} ${g.clock}` : period;
}

// The API serves ISO timestamps in the Mac's zone; the phone renders them in its own. The server
// render and the client render can differ, so hydration warnings are suppressed on purpose.
// A null kickoff means two different things: the player is on bye, or ESPN has no schedule entry
// for him at all. `ffl/outlook.py::_kickoff_text` draws that line, so the card header must too.
export function Kickoff({ iso, bye, game }: { iso: string | null; bye: boolean; game?: GameState }) {
  if (game) return <span>{gameClock(game)}</span>;
  if (!iso) return <span>{bye ? "BYE" : "not scheduled"}</span>;
  const d = new Date(iso);
  return (
    <span suppressHydrationWarning>
      {d.toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}
    </span>
  );
}

// A time of day in the viewer's zone (the play feed's stamp).
export function Clock({ iso }: { iso: string }) {
  return <span suppressHydrationWarning>{new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}</span>;
}

export function Ago({ iso }: { iso: string }) {
  // The clock is impure, so read it once in a state initialiser rather than in the render body
  // (react-hooks/purity): the age is fixed for the life of the render, which is what a
  // server-rendered page wants anyway.
  const [now] = useState(() => Date.now());
  const mins = Math.max(0, Math.round((now - new Date(iso).getTime()) / 60_000));
  const text = mins < 60 ? `${mins}m ago` : mins < 48 * 60 ? `${Math.round(mins / 60)}h ago` : `${Math.round(mins / 1440)}d ago`;
  return <span suppressHydrationWarning>{text}</span>;
}
