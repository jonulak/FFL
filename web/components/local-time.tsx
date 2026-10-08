"use client";

import { useSyncExternalStore } from "react";
import type { GameState, Player } from "@/lib/api";
import { minutesSince, shortAge } from "@/lib/format";
import { TeamLogo } from "./player-media";

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

// The kickoff as the game line sets it: "Sun 1:00 PM", in the viewer's zone.
export function KickoffTime({ iso }: { iso: string }) {
  return (
    <span suppressHydrationWarning>
      {new Date(iso).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}
    </span>
  );
}

// A player's game on one line, the clubs as their marks: `[DET] @ [GB] Sun 1:00 PM` before
// kickoff (`vs` when his club hosts, `@` when it travels, a dot when the venue is unknown), and
// once the game is on each club's score before its mark: `24 [PIT] @ 27 [CLE] Final`, the
// leading score in full ink. His own club always comes first. The codes are read to a screen
// reader, and stand in for a mark that fails to load.
export function GameLine({ p, size = 14, className = "" }: { p: Pick<Player, "team" | "opponent" | "home" | "kickoff" | "bye" | "game">; size?: number; className?: string }) {
  if (p.bye) return <span className={className}>BYE</span>;
  if (!p.opponent) return <span className={className}>{p.kickoff ? <KickoffTime iso={p.kickoff} /> : "not scheduled"}</span>;
  const g = p.game;
  const own = g?.score?.[p.team];
  const other = g ? Object.entries(g.score ?? {}).find(([t]) => t !== p.team)?.[1] : undefined;
  const scored = own != null && other != null;
  const sep = p.home === true ? "vs" : p.home === false ? "@" : "·";
  const ink = (mine: number, theirs: number) => (mine > theirs ? "font-semibold text-foreground" : "text-foreground/80");
  const mark = (abbr: string) => <TeamLogo abbr={abbr} size={size} fallback className="shrink-0 font-medium" />;
  return (
    <span className={`inline-flex items-center gap-x-1 whitespace-nowrap tabular-nums ${className}`}>
      <span className="sr-only">{p.team} {p.home === true ? "vs" : "at"} {p.opponent}</span>
      {scored && <span className={ink(own, other)}>{own}</span>}
      {mark(p.team)}
      <span aria-hidden>{sep}</span>
      {scored && <span className={ink(other, own)}>{other}</span>}
      {mark(p.opponent)}
      <span className="ml-0.5">
        {g
          ? <span className={g.status === "in" ? "font-medium text-foreground/85" : undefined}>{gameClock(g)}</span>
          : p.kickoff ? <KickoffTime iso={p.kickoff} /> : null}
      </span>
    </span>
  );
}

// A time of day in the viewer's zone (the play feed's stamp).
export function Clock({ iso }: { iso: string }) {
  return <span suppressHydrationWarning>{new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}</span>;
}

// One clock for every age on the page, ticking once a minute while anything reads it, so a page left open on the
// phone keeps telling the truth about how old its outlooks are. The server renders the time of its request; the
// client takes over on hydration.
const boot = Date.now();
let now = boot;
const readers = new Set<() => void>();
let ticker: ReturnType<typeof setInterval> | null = null;
function subscribe(onTick: () => void) {
  readers.add(onTick);
  if (!ticker) {
    const tick = () => { now = Date.now(); readers.forEach((r) => r()); };
    tick();
    ticker = setInterval(tick, 60_000);
  }
  return () => {
    readers.delete(onTick);
    if (readers.size === 0 && ticker) { clearInterval(ticker); ticker = null; }
  };
}
export function useNow(): number {
  return useSyncExternalStore(subscribe, () => now, () => boot);
}

export function Ago({ iso }: { iso: string }) {
  const age = shortAge(minutesSince(iso, useNow()));
  return <span suppressHydrationWarning>{age === "now" ? "just now" : `${age} ago`}</span>;
}
