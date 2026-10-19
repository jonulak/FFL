"use client";

import { ArrowUpRight } from "lucide-react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { DialogClose } from "@/components/ui/dialog";
import type { PlayerNews } from "@/lib/api";
import { filterHref, playerNewsHref, statusLabel, storyStamp, type Recent } from "@/lib/news";
import { NAME_LINK } from "@/lib/player-log";
import { Ago } from "./local-time";
import { NewsMute } from "./news-mute";
import { Line } from "./news-story";
import { Heading } from "./player-dialog";

// The player dialog's News section: his newest three stories (`recent`, recentFor's), each the headline (two lines at
// most, opening the story in the feed and closing the modal), the stamp (storyStamp: the source, linked off-site, or
// Updated) and its age, then his line (the arrow and Haiku's words, the feed's own Line); last, the way on to all his
// news and the push mute. The overview is left to the feed, one tap away. The caller leaves the section out when he
// has no story.
export function RecentNews({ d, recent }: { d: PlayerNews; recent: Recent }) {
  const p = d.player;
  return (
    <section aria-labelledby="season-news" className="log-in space-y-1.5 px-4 pt-2 pb-5">
      <Heading id="season-news" title="News" />
      <ul className="divide-y divide-foreground/10">
        {recent.map(({ story: s, row }) => {
          const label = statusLabel(s.status);
          const stamp = storyStamp(s);
          return (
            <li key={s.id} className="space-y-1.5 py-2.5">
              <DialogClose nativeButton={false} render={<Link href={filterHref("all", false, s.id)} />}
                className={`line-clamp-2 rounded-sm text-[0.9375rem] font-medium leading-snug text-balance outline-none focus-visible:ring-2 focus-visible:ring-ring/60 ${s.status === "retracted" ? "text-foreground/60" : ""} ${NAME_LINK}`}>
                {s.headline}
              </DialogClose>
              <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs leading-4 text-muted-foreground">
                {stamp.updated
                  ? <span className="font-medium text-foreground/85">Updated</span>
                  : stamp.cite?.url
                    ? <a href={stamp.cite.url} target="_blank" rel="noreferrer" className={`inline-flex items-center gap-0.5 font-medium text-foreground/85 ${NAME_LINK}`}>
                        {stamp.cite.source}<ArrowUpRight aria-hidden className="size-3" />
                      </a>
                    : stamp.cite && <span className="font-medium text-foreground/85">{stamp.cite.source}</span>}
                {(stamp.updated || stamp.cite) && <span aria-hidden>·</span>}
                <Ago iso={stamp.at} />
                {label && <Badge variant="muted" shape="chip" className="ml-1">{label}</Badge>}
              </p>
              {/* onPage: the one way on to his news is the section's, below */}
              <Line r={row} dim={s.status === "retracted"} onPage />
            </li>
          );
        })}
      </ul>
      <NewsMute id={p.player_id} muted={d.muted} name={p.name} className="pt-1" lead={
        <DialogClose nativeButton={false} render={<Link href={playerNewsHref(p.player_id)} />}
          className={`inline-flex min-h-10 items-center gap-1 rounded-sm font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-foreground/55 outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60 sm:min-h-8 ${NAME_LINK}`}>
          All {p.name} news<span aria-hidden>→</span>
        </DialogClose>
      } />
    </section>
  );
}
