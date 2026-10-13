"use client";

import { useState } from "react";
import { headshotSrcSet, headshotUrl, logoSrcSet, logoUrl, team } from "@/lib/teams";

// Plain <img> on purpose: the files come straight from ESPN's CDN and the launchd box has no
// business proxying them through the Next image optimizer. Every image degrades to the team
// logo, and the logo to nothing — a broken-image glyph never reaches the card.

/* eslint-disable @next/next/no-img-element */

// A club mark that picks ESPN's dark-ground cut under a dark system theme. The page follows
// prefers-color-scheme (no toggle), so <picture> can choose without JS.
// `fallback`: when the mark stands in for the club's name (the Matchup game line), a failed
// load leaves the abbreviation rather than nothing.
export function TeamLogo({ abbr, className, size = 20, fallback = false }: { abbr: string; className?: string; size?: number; fallback?: boolean }) {
  const [gone, setGone] = useState(false);
  if (gone) return fallback ? <span className={className}>{abbr}</span> : null;
  return (
    <picture className={className}>
      <source srcSet={logoSrcSet(abbr, "dark", size)} media="(prefers-color-scheme: dark)" />
      <img
        src={logoUrl(abbr, "light", size)}
        srcSet={logoSrcSet(abbr, "light", size)}
        alt=""
        width={size}
        height={size}
        loading="lazy"
        decoding="async"
        className="block object-contain"
        style={{ width: size, height: size }}
        onError={() => setGone(true)}
      />
    </picture>
  );
}

// The team-colour panel a player is cut out on: club gradient, a watermark of the mark, and the
// portrait standing at the foot. D/ST cards (negative ids) and any missing portrait show the
// mark full size instead, so a card never shows an empty panel.
// `tile`: the row-scale square on the Matchup page; the portrait is scaled down so the head stays in frame.
export function TeamPanel({ abbr, playerId, name, muted, tile = false }: { abbr: string; playerId: number; name: string; muted: boolean; tile?: boolean }) {
  const t = team(abbr);
  // the CSS width the portrait and the watermark are drawn at: a tile is about 60 px, the art overhangs it
  const art = tile ? 80 : 200;
  const src = headshotUrl(playerId, art);
  const [portrait, setPortrait] = useState(src !== null);
  return (
    <div
      aria-hidden
      className={`relative h-full overflow-hidden transition-[filter,opacity] duration-500 ${muted ? "saturate-[.35] opacity-80" : ""}`}
      style={{
        background: `linear-gradient(168deg, ${t.primary} 0%, color-mix(in oklab, ${t.primary} 62%, ${t.secondary}) 100%)`,
        boxShadow: "inset -1px 0 0 rgb(255 255 255 / 0.08)",
      }}
    >
      {/* watermark: the mark oversized and trailing off the panel's left edge */}
      <img
        src={logoUrl(abbr, "light", art)}
        srcSet={logoSrcSet(abbr, "light", art)}
        alt=""
        loading="lazy"
        decoding="async"
        className={`pointer-events-none absolute max-w-none select-none ${
          portrait ? "-left-[35%] top-[18%] w-[130%] opacity-[0.16] sm:top-[6%]" : "left-1/2 top-1/2 w-[72%] -translate-x-1/2 -translate-y-1/2 opacity-95 drop-shadow-[0_6px_10px_rgb(0_0_0_/_0.45)]"
        }`}
      />
      {portrait && src && (
        <img
          src={src}
          srcSet={headshotSrcSet(playerId, art)}
          alt={name}
          loading="lazy"
          decoding="async"
          className={`absolute bottom-0 left-1/2 max-w-none -translate-x-1/2 drop-shadow-[0_8px_12px_rgb(0_0_0_/_0.5)] ${tile ? "w-[128%]" : "w-[210%] sm:w-[172%]"}`}
          onError={() => setPortrait(false)}
        />
      )}
    </div>
  );
}
