"use client";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";

// A fantasy team's mark: the owner's ESPN upload over an abbreviation tile, as a shadcn Avatar
// squared to the board's 6px corner. The fallback tile is drawn until the image has loaded, so a
// missing, slow or refused file (ESPN's own uploads answer 401 outside its site) never shows a
// broken-image glyph. Plain <img> underneath, like the club marks in player-media.tsx: nothing
// here should proxy a CDN asset.
export function ClubLogo({ src, abbrev, size = 32, className = "" }: { src: string; abbrev: string; size?: number; className?: string }) {
  return (
    <Avatar aria-hidden className={`rounded-md after:hidden ${className}`} style={{ width: size, height: size }}>
      {src && <AvatarImage src={src} alt="" width={size} height={size} loading="lazy" decoding="async" className="rounded-md" />}
      <AvatarFallback className="rounded-md bg-foreground/10 font-display text-[11px] font-semibold uppercase tracking-[0.06em] text-foreground/75">
        {abbrev.slice(0, 4)}
      </AvatarFallback>
    </Avatar>
  );
}
