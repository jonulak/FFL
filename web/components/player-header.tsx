"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import type { Player } from "@/lib/api";
import { injuryTone } from "@/lib/format";
import { headerInjury } from "@/lib/player-log";
import { headshotUrl, logoUrl, team } from "@/lib/teams";
import { GameLine } from "./local-time";

/* eslint-disable @next/next/no-img-element -- ESPN's CDN, as player-media.tsx */

// What a row knows about a player when it opens his modal; the header draws from it at once,
// before any fetch. Every Player-shaped row satisfies it.
export type PlayerSeed = Pick<Player, "player_id" | "name" | "pos" | "team">
  & Partial<Pick<Player, "injury" | "injury_note" | "return_week" | "opponent" | "home" | "kickoff" | "bye" | "game">>;

const TAG = { ok: "secondary", warn: "warn", bad: "out" } as const;
// ESPN says ACTIVE for a player and NORMAL for a defence; neither is news.
const HEALTHY = new Set(["ACTIVE", "NORMAL"]);

// Both player modals open on this: the roster card's team-colour panel laid down as a band across
// the top, the portrait standing at its left foot and the club mark trailing off the right edge,
// the name set on a caption scrim at the foot (white type on any club's colour, the Steelers'
// gold included), and beneath the band, on the dialog's own surface, this week's game.
// The dialog's close button sits on the band: the DialogContent carries BAND_CLOSE.
export function PlayerHeader({ p, week }: { p: PlayerSeed; week?: number }) {
  const t = team(p.team);
  const src = headshotUrl(p.player_id);
  const [portrait, setPortrait] = useState(src !== null);  const hurt = p.injury != null && !HEALTHY.has(p.injury);
  const scheduled = p.opponent !== undefined || p.bye !== undefined;
  return (
    <DialogHeader className="gap-0">
      <div
        className="relative isolate h-28 overflow-hidden sm:h-32"
        style={{ background: `linear-gradient(168deg, ${t.primary} 0%, color-mix(in oklab, ${t.primary} 62%, ${t.secondary}) 100%)` }}
      >
        <img src={logoUrl(p.team)} alt="" aria-hidden loading="lazy" decoding="async"
          className="pointer-events-none absolute -top-10 -right-10 -z-10 w-56 max-w-none select-none opacity-[0.18]" />
        {/* Portrait, scrim and caption each hold a compositing layer of their own from the first
            frame (will-change). Left to iOS Safari, the portrait is lifted onto a layer whenever
            something in the modal animates (its old fade, the log rows arriving, a fold) and that
            layer draws over the scrim until the animation ends, so the portrait landed bright and
            then visibly darkened. With the three layers fixed, their order never changes. */}
        <div aria-hidden className="absolute bottom-0 left-2 h-full w-28 overflow-hidden will-change-transform sm:left-3 sm:w-32">
          {portrait && src ? (
            // No fade of its own: the dialog's entrance already carries it in.
            <img src={src} alt="" decoding="async" onError={() => setPortrait(false)}
              className="absolute bottom-0 left-1/2 h-[112%] w-auto max-w-none -translate-x-1/2 drop-shadow-[0_8px_12px_rgb(0_0_0_/_0.5)]" />
          ) : (
            <img src={logoUrl(p.team)} alt="" decoding="async"
              className="absolute top-1/2 left-1/2 w-20 -translate-x-1/2 -translate-y-1/2 drop-shadow-[0_6px_10px_rgb(0_0_0_/_0.45)] sm:w-24" />
          )}
        </div>
        {/* the caption scrim: dark enough at the foot for white type on every club's colour */}
        <div aria-hidden className="absolute inset-0 bg-linear-to-t from-black/75 via-black/30 via-55% to-transparent will-change-transform" />
        <div className="absolute right-12 bottom-0 left-32 pb-3 will-change-transform sm:left-38">
          <DialogTitle className="line-clamp-2 text-balance font-display text-[1.65rem] font-semibold leading-none tracking-tight text-white">
            {p.name}
          </DialogTitle>
          <DialogDescription render={<div />} className="mt-1.5 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-white/85">
            <span className="font-semibold text-white">{p.pos}</span>
            <span aria-hidden>·</span>
            <span>{t.name || p.team}</span>
            {hurt && (
              <Badge variant={TAG[injuryTone(p.injury!)]} shape="chip" title={p.injury_note ?? undefined} className="ml-0.5">
                {headerInjury(p.injury!.replaceAll("_", " "), p.return_week ?? null, week)}
              </Badge>
            )}
          </DialogDescription>
        </div>
      </div>
      {scheduled && (
        <div className="border-b border-foreground/10 px-4 py-2 text-xs text-muted-foreground">
          <GameLine p={{ team: p.team, opponent: p.opponent ?? null, home: p.home ?? null, kickoff: p.kickoff ?? null, bye: p.bye ?? false, game: p.game }} size={16} />
        </div>
      )}
    </DialogHeader>
  );
}

// The close button as it sits on the band: white, with a white wash on hover.
export const BAND_CLOSE = "[&>[data-slot=dialog-close]]:z-10 [&>[data-slot=dialog-close]]:text-white [&>[data-slot=dialog-close]]:hover:bg-white/15 [&>[data-slot=dialog-close]]:hover:text-white";
