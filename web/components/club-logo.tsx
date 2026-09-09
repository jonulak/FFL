"use client";

import { useState } from "react";

/* eslint-disable @next/next/no-img-element */

// A fantasy team's mark: the owner's ESPN upload over an abbreviation tile. The tile is always
// drawn and the image fades in over it once it has loaded, so a missing, slow or refused file
// (ESPN's own uploads answer 401 outside its site) never shows a broken-image glyph. Plain
// <img>, like the club marks in player-media.tsx: nothing here should proxy a CDN asset.
export function ClubLogo({ src, abbrev, size = 32, className = "" }: { src: string; abbrev: string; size?: number; className?: string }) {
  const [state, setState] = useState<"loading" | "ok" | "gone">(src ? "loading" : "gone");
  return (
    <span
      aria-hidden
      className={`relative flex shrink-0 items-center justify-center overflow-hidden rounded-md font-display text-[11px] font-semibold uppercase tracking-[0.06em] text-foreground/75 ${state === "ok" ? "" : "bg-foreground/10"} ${className}`}
      style={{ width: size, height: size }}
    >
      {abbrev.slice(0, 4)}
      {state !== "gone" && (
        <img
          src={src}
          alt=""
          width={size}
          height={size}
          loading="lazy"
          decoding="async"
          className={`absolute inset-0 size-full object-cover transition-opacity duration-200 ${state === "ok" ? "opacity-100" : "opacity-0"}`}
          // a server-rendered image can finish before hydration, and then no load event ever fires
          ref={(el) => { if (el?.complete) setState(el.naturalWidth > 0 ? "ok" : "gone"); }}
          onLoad={() => setState("ok")}
          onError={() => setState("gone")}
        />
      )}
    </span>
  );
}
