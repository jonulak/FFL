"use client";

import { useEffect, useRef, useState } from "react";
import { cn } from "cn";

/* eslint-disable @next/next/no-img-element */

// A fantasy team's mark: the owner's ESPN upload, or an abbreviation tile when there is none,
// squared to the board's 6px corner. Hand-rolled rather than shadcn's Avatar: Base UI's
// Avatar.Image renders nothing until a JS probe in an effect reports the file loaded, so every
// page load painted the tile and then faded the logo in, cache or no cache. Here the <img> is in
// the server HTML and the browser paints a cached file with the first frame. A missing or refused
// file (ESPN's own uploads answer 401 outside its site) swaps in the tile, so a broken-image glyph
// never shows; an error that fired before hydration is caught by the mount check. Plain <img>, like
// the club marks in player-media.tsx: nothing here should proxy a CDN asset.
export function ClubLogo({ src, abbrev, size = 32, className = "" }: { src: string; abbrev: string; size?: number; className?: string }) {
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const img = useRef<HTMLImageElement>(null);
  useEffect(() => {
    const el = img.current;
    if (el?.complete && el.naturalWidth === 0) setFailedSrc(src);
  }, [src]);
  const showImg = !!src && failedSrc !== src;
  return (
    <span
      aria-hidden
      className={cn("relative flex shrink-0 select-none items-center justify-center overflow-hidden rounded-md", className)}
      style={{ width: size, height: size }}
    >
      {showImg ? (
        <img
          ref={img}
          src={src}
          alt=""
          width={size}
          height={size}
          decoding="async"
          className="size-full rounded-md object-cover"
          onError={() => setFailedSrc(src)}
        />
      ) : (
        <span className="flex size-full items-center justify-center rounded-md bg-foreground/10 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.06em] text-foreground/75">
          {abbrev.slice(0, 4)}
        </span>
      )}
    </span>
  );
}
