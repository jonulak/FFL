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

// The score with the player's own team first: "JAX 14, CLE 3".
export function gameScore(g: GameState, team: string): string | null {
  const teams = Object.keys(g.score ?? {});
  if (teams.length < 2) return null;
  const ordered = [team, ...teams.filter((t) => t !== team)].filter((t) => t in g.score);
  return ordered.map((t) => `${t} ${g.score[t]}`).join(", ");
}

// The score as a broadcast app sets it, his own club first: "27-7".
export function gameScoreShort(g: GameState, team: string): string | null {
  const teams = Object.keys(g.score ?? {});
  if (teams.length < 2 || !(team in g.score)) return null;
  const other = teams.find((t) => t !== team);
  return other ? `${g.score[team]}-${g.score[other]}` : null;
}

// The API serves ISO timestamps in the Mac's zone; the phone renders them in its own. The server
// render and the client render can differ, so hydration warnings are suppressed on purpose.
// A null kickoff means two different things: the player is on bye, or ESPN has no schedule entry
// for him at all. `ffl/outlook.py::_kickoff_text` draws that line, so the card header must too.
// With `game` (and `team`) the line becomes the clock and the score instead.
// `compact` is the phone's form, the way a broadcast app sets it after the opponent: the score as
// `27-7` with his own club first, then `Final`; or the clock and the score while the game is on.
export function Kickoff({ iso, bye, game, team, compact = false }: { iso: string | null; bye: boolean; game?: GameState; team?: string; compact?: boolean }) {
  if (game) {
    const score = team ? (compact ? gameScoreShort(game, team) : gameScore(game, team)) : null;
    const clock = <span className={game.status === "in" ? "font-medium text-foreground/85" : undefined}>{gameClock(game)}</span>;
    if (compact) {
      return (
        <span className="whitespace-nowrap">
          {game.status === "post" ? <>{score && <>{score} </>}{clock}</> : <>{clock}{score && <> <span aria-hidden>· </span>{score}</>}</>}
        </span>
      );
    }
    return (
      <span>
        {clock}
        {/* dot and score are one unit: a narrow column breaks before them, and the wrapped line leads with the dot */}
        {score && <> <span className="whitespace-nowrap"><span aria-hidden>· </span>{score}</span></>}
      </span>
    );
  }
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
