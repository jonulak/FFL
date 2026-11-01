"use client";

import { ArrowUpRight, ChevronDown, MessageCircleQuestion } from "lucide-react";
import Link from "@/components/league-link";
import { Badge } from "@/components/ui/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { NewsRow, Story } from "@/lib/api";
import { shortName } from "@/lib/format";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { chipRows, EFFECT_WORD, ownerTag, playerNewsHref, statusLabel, storyStamp, type NewsFilter } from "@/lib/news";
import { NAME_LINK } from "@/lib/player-log";
import { headshotArt } from "@/lib/teams";
import { Ago } from "./local-time";
import { PlayerTrigger } from "./player-dialog";
import { TeamLogo } from "./player-media";

// One story on the wire: where it came from and when, the headline and overview, then a strip of the players it
// moves, each his portrait, a name and who holds him, the chip coloured by the effect: lime washed for up, red
// washed for down, a bare outline for no change (the board's START and OUT hues, the one place they mean
// something other than the call). A retracted story stays in the feed with every chip bare and its portraits
// greyed, since nothing it said stands.

const TAG = "font-display text-[0.6875rem] font-semibold uppercase tracking-[0.1em]";

type Effect = NewsRow["effect"];
// The chip's face per effect: wash, ring, and a stronger wash under the pointer or while his line is open.
const TONE: Record<Effect, string> = {
  up: "bg-start/22 ring-start/80 hover:bg-start/34 data-popup-open:bg-start/40 dark:bg-start/16 dark:ring-start/55 dark:hover:bg-start/26 dark:data-popup-open:bg-start/32",
  down: "bg-out/12 ring-out/60 hover:bg-out/20 data-popup-open:bg-out/24 dark:bg-out/18 dark:ring-out/60 dark:hover:bg-out/28 dark:data-popup-open:bg-out/34",
  none: "ring-foreground/14 hover:bg-foreground/[0.06] data-popup-open:bg-foreground/[0.08]",
};
const SWATCH: Record<Effect, string> = { up: "bg-start", down: "bg-out", none: "bg-foreground/30" };

// The effect beside its word in his line: a square of the chip's colour.
function Swatch({ effect, dim }: { effect: Effect; dim: boolean }) {
  return <span aria-hidden className={`size-2 shrink-0 rounded-[2px] ${dim ? "bg-foreground/25" : SWATCH[effect]}`} />;
}

// His portrait, cut at the chest, on a quiet tile; a defence or a missing portrait shows the club mark.
function Face({ r, dim }: { r: NewsRow; dim: boolean }) {
  const src = headshotArt(r.player_id, 28);
  return (
    <Avatar className={`size-6 overflow-hidden rounded-[3px] bg-foreground/[0.08] after:hidden sm:size-[1.375rem] ${dim ? "opacity-70 grayscale" : ""}`}>
      {src && <AvatarImage src={src} alt="" className="rounded-none object-cover object-top" />}
      <AvatarFallback className="rounded-none bg-transparent">{r.team ? <TeamLogo abbr={r.team} size={18} /> : null}</AvatarFallback>
    </Avatar>
  );
}

// Haiku wrote the line but doubts the tagger's arrow: a mark beside the name, said in words in the line itself.
function Doubt({ className = "" }: { className?: string }) {
  return <MessageCircleQuestion aria-label="Haiku doubts this arrow" className={`size-3.5 shrink-0 text-foreground/55 ${className}`} />;
}

function OwnerTag({ r }: { r: NewsRow }) {
  return <span className={`${TAG} ${r.mine ? "text-foreground" : "text-muted-foreground"}`}>{ownerTag(r)}</span>;
}

// His line: what the effect is for, in Haiku's words, and the way on to his record and his news.
export function Line({ r, dim, onPage = false }: { r: NewsRow; dim: boolean; onPage?: boolean }) {
  return (
    <div className="space-y-1.5">
      <p className="flex flex-wrap items-center gap-x-1.5 text-xs leading-4 text-muted-foreground">
        <Swatch effect={r.effect} dim={dim} />
        <span className="font-medium text-foreground/85">{EFFECT_WORD[r.effect]}</span>
        {r.kind && <><span aria-hidden>·</span><span>{r.kind}</span></>}
      </p>
      <p className="max-w-[68ch] text-sm leading-snug">{r.reason ?? <span className="text-muted-foreground">No line written for him yet.</span>}</p>
      {r.flag && (
        <p className="flex items-center gap-1.5 text-xs leading-4 text-muted-foreground"><Doubt />Haiku doubts this arrow: the sources may not bear it out.</p>
      )}
      {!onPage && (
        <Link href={playerNewsHref(r.player_id)} className={`inline-flex min-h-8 items-center gap-1 font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-foreground/55 hover:text-foreground ${NAME_LINK}`}>
          All his news<span aria-hidden>→</span>
        </Link>
      )}
    </div>
  );
}

const seedOf = (r: NewsRow) => ({ player_id: r.player_id, name: r.name, pos: r.pos ?? "", team: r.team ?? "" });

// A player chip: his portrait, short name and the owner tag on the effect's colour; a tap opens his line over the
// feed. `tagged` is false when the owners read failed: every row then comes as a free agent, so no tag is shown
// rather than a false FA.
function Chip({ r, dim, tagged }: { r: NewsRow; dim: boolean; tagged: boolean }) {
  return (
    <Popover>
      <PopoverTrigger
        className={`inline-flex min-h-8 items-center gap-1.5 rounded-sm py-1 pr-2 pl-1 text-left outline-none ring-1 ring-inset transition-colors focus-visible:ring-2 focus-visible:ring-ring sm:min-h-7 ${TONE[dim ? "none" : r.effect]}`}
        aria-label={`${r.name}, ${EFFECT_WORD[r.effect]}${tagged ? `, ${ownerTag(r)}` : ""}${r.flag ? ", Haiku doubts this arrow" : ""}: his line`}
      >
        <Face r={r} dim={dim} />
        <span className={`font-display text-[0.9375rem] font-semibold leading-none tracking-tight ${dim ? "text-foreground/60" : ""}`}>{shortName(r.name)}</span>
        {r.flag && <Doubt />}
        {tagged && <OwnerTag r={r} />}
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[min(20rem,calc(100vw-2rem))] gap-2 p-3">
        <div>
          <PlayerTrigger p={seedOf(r)} className={`rounded-sm font-display text-[1.125rem] font-semibold leading-tight tracking-tight ${NAME_LINK}`}>{r.name}</PlayerTrigger>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-xs leading-4 text-muted-foreground">
            {r.pos && <span className="font-semibold text-foreground/80">{r.pos}</span>}
            {r.pos && r.team && <span aria-hidden>·</span>}
            {r.team && <span>{r.team}</span>}
            {tagged && <><span aria-hidden>·</span><span>{r.mine ? "on my roster" : r.owner ? (r.owner.name ?? ownerTag(r)) : "free agent"}</span></>}
          </p>
        </div>
        <Line r={r} dim={dim} />
      </PopoverContent>
    </Popover>
  );
}

// On a player's own page his row is the story's point: the chip opened out into his line.
function OpenRow({ r, dim }: { r: NewsRow; dim: boolean }) {
  return (
    <div className="rounded-sm border border-foreground/12 px-2.5 py-2">
      <Line r={r} dim={dim} onPage />
    </div>
  );
}


function Chips({ rows, dim, tagged }: { rows: NewsRow[]; dim: boolean; tagged: boolean }) {
  if (!rows.length) return null;
  return <div className="flex flex-wrap items-center gap-1.5">{rows.map((r) => <Chip key={r.player_id} r={r} dim={dim} tagged={tagged} />)}</div>;
}

// the foot's ways on share Full story's label register, so the line reads as one strip of controls
const FOOT_LINK = "inline-flex min-h-8 items-center gap-1 rounded-sm font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-foreground/55 outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60";
const CHEVRON = "size-3.5 transition-transform duration-300 ease-(--ease-arrive) group-aria-expanded:rotate-180 motion-reduce:transition-none";

// The story's foot, one line: the full story and "+N sources", a Collapsible whose root is `display: contents` so its
// trigger shares the line and the rest of the reporting (each source linked, by time) opens below it.
function Foot({ s }: { s: Story }) {
  const n = s.sources.length;
  if (!s.first?.url && !n) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-4">
      {s.first?.url && (
        <a href={s.first.url} target="_blank" rel="noreferrer"
          className={`order-1 inline-flex min-h-8 items-center gap-1 font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-foreground/55 hover:text-foreground ${NAME_LINK}`}>
          Full story<ArrowUpRight aria-hidden className="size-3.5" />
        </a>
      )}
      {n > 0 && (
        <Collapsible className="contents">
          <CollapsibleTrigger className={`group order-2 ${FOOT_LINK}`}>
            +{n} {n === 1 ? "source" : "sources"}<ChevronDown aria-hidden className={CHEVRON} />
          </CollapsibleTrigger>
          <CollapsibleContent className="order-last basis-full">
            <ul className="mt-1 space-y-1 border-l border-foreground/12 pl-3">
              {s.sources.map((c, i) => (
                <li key={i} className="flex flex-wrap items-baseline gap-x-1.5 text-xs leading-5 text-muted-foreground">
                  {c.url
                    ? <a href={c.url} target="_blank" rel="noreferrer" className={`font-medium text-foreground/85 ${NAME_LINK}`}>{c.source}</a>
                    : <span className="font-medium text-foreground/85">{c.source}</span>}
                  <span aria-hidden>·</span>
                  <Ago iso={c.at} />
                </li>
              ))}
            </ul>
          </CollapsibleContent>
        </Collapsible>
      )}
    </div>
  );
}

// `focus`: the player page's player, whose row (the only one the API sends there) opens out into his line.
// `target`: the story a push opened, washed in ink and scrolled to by the feed.
// `tagged`: the owners read succeeded, so the owner tags are true.
// `filter`: the feed's; under My players only my own chips show, under Free agents only the free agents'. A player the
// story names but does not move gets no chip anywhere.
export function NewsStory({ s, focus, target = false, tagged = true, filter = "all" }: {
  s: Story; focus?: number; target?: boolean; tagged?: boolean; filter?: NewsFilter;
}) {
  const label = statusLabel(s.status);
  const retracted = s.status === "retracted";
  const chips = chipRows(s.players, filter);
  const stamp = storyStamp(s);
  return (
    <li id={`story-${s.id}`} data-target={target || undefined} className="news-story scroll-mt-[calc(var(--nav-h)+1rem)] space-y-2.5 px-3 py-4 sm:px-4">
      <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs leading-4 text-muted-foreground">
        {stamp.updated && <><span className="font-medium text-foreground/85">Updated</span><span aria-hidden>·</span></>}
        {stamp.cite && <><span className="font-medium text-foreground/85">{stamp.cite.source}</span><span aria-hidden>·</span></>}
        <Ago iso={stamp.at} />
        {label && <Badge variant="muted" shape="chip" className="ml-1">{label}</Badge>}
        {s.status === "corrected" && s.status_note && <span>by {s.status_note}</span>}
      </p>
      <div className="space-y-1">
        <h2 className={`max-w-[40rem] text-[1.0625rem] font-semibold leading-[1.3] tracking-[-0.01em] text-balance sm:text-[1.125rem] ${retracted ? "text-foreground/60" : ""}`}>{s.headline}</h2>
        {s.overview && <p className="max-w-[40rem] text-sm leading-[1.55] text-pretty text-muted-foreground">{s.overview}</p>}
      </div>
      {focus != null
        ? s.players.filter((r) => r.player_id === focus).map((r) => <OpenRow key={r.player_id} r={r} dim={retracted} />)
        : <Chips rows={chips} dim={retracted} tagged={tagged} />}
      <Foot s={s} />
    </li>
  );
}
