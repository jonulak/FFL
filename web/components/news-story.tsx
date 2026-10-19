"use client";

import { ArrowUpRight, ChevronDown, MessageCircleQuestion } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { NewsRow, Story } from "@/lib/api";
import { shortName } from "@/lib/format";
import { arrow, EFFECT_WORD, ownerTag, playerNewsHref, splitRows, statusLabel } from "@/lib/news";
import { NAME_LINK } from "@/lib/player-log";
import { Ago } from "./local-time";
import { PlayerTrigger } from "./player-dialog";

// One story on the wire: where it came from and when, the headline and overview, then a strip of the players it
// moves, each an arrow, a name and who holds him. The arrow is the glyph alone, in ink (the Three Verdicts Rule
// keeps lime, amber and red for the call): ▲ full ink, ▼ a step back, ● muted. A retracted story stays in the
// feed with every arrow stepped back further, since nothing it said stands.

const TAG = "font-display text-[0.6875rem] font-semibold uppercase tracking-[0.1em]";

// ▲/▼/● in ink steps; a retracted story's arrows all recede.
function Arrow({ effect, dim }: { effect: NewsRow["effect"]; dim: boolean }) {
  const ink = dim ? "text-foreground/35" : effect === "up" ? "text-foreground" : effect === "down" ? "text-foreground/70" : "text-foreground/40";
  return (
    <span className={`text-[0.625rem] leading-none ${ink}`}>
      <span aria-hidden>{arrow(effect)}</span>
      <span className="sr-only">{EFFECT_WORD[effect]}</span>
    </span>
  );
}

// Haiku wrote the line but doubts Jev's arrow: a mark beside the name, said in words in the line itself.
function Doubt({ className = "" }: { className?: string }) {
  return <MessageCircleQuestion aria-label="Haiku doubts this arrow" className={`size-3.5 shrink-0 text-foreground/55 ${className}`} />;
}

function OwnerTag({ r }: { r: NewsRow }) {
  return <span className={`${TAG} ${r.mine ? "text-foreground" : "text-muted-foreground"}`}>{ownerTag(r)}</span>;
}

// His line: what the arrow is for, in Haiku's words, and the way on to his record and his news.
function Line({ r, dim, onPage = false }: { r: NewsRow; dim: boolean; onPage?: boolean }) {
  return (
    <div className="space-y-1.5">
      <p className="flex flex-wrap items-center gap-x-1.5 text-xs leading-4 text-muted-foreground">
        <Arrow effect={r.effect} dim={dim} />
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

// A player chip: the arrow, his short name and the owner tag; a tap opens his line over the feed.
function Chip({ r, dim, enter = false }: { r: NewsRow; dim: boolean; enter?: boolean }) {
  return (
    <Popover>
      <PopoverTrigger
        className={`inline-flex min-h-8 items-center gap-1.5 rounded-sm border px-2 py-1 text-left outline-none transition-colors hover:bg-foreground/[0.06] focus-visible:ring-2 focus-visible:ring-ring/60 data-popup-open:bg-foreground/[0.08] sm:min-h-7 ${enter ? "fx-in" : ""} ${r.mine ? "border-foreground/30 bg-foreground/[0.04]" : "border-foreground/12"}`}
        aria-label={`${r.name}, ${EFFECT_WORD[r.effect]}, ${ownerTag(r)}${r.flag ? ", Haiku doubts this arrow" : ""}: his line`}
      >
        <Arrow effect={r.effect} dim={dim} />
        <span className={`font-display text-[0.9375rem] font-semibold leading-none tracking-tight ${dim ? "text-foreground/60" : ""}`}>{shortName(r.name)}</span>
        {r.flag && <Doubt />}
        <OwnerTag r={r} />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[min(20rem,calc(100vw-2rem))] gap-2 p-3">
        <div>
          <PlayerTrigger p={seedOf(r)} className={`rounded-sm font-display text-[1.125rem] font-semibold leading-tight tracking-tight ${NAME_LINK}`}>{r.name}</PlayerTrigger>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-xs leading-4 text-muted-foreground">
            {r.pos && <span className="font-semibold text-foreground/80">{r.pos}</span>}
            {r.pos && r.team && <span aria-hidden>·</span>}
            {r.team && <span>{r.team}</span>}
            <span aria-hidden>·</span>
            <span>{r.mine ? "on my roster" : r.owner ? (r.owner.name ?? ownerTag(r)) : "free agent"}</span>
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

// The ● rows (named by the source, not moved by it) join the strip only when "+k no change" is opened.
function Chips({ rows, dim }: { rows: NewsRow[]; dim: boolean }) {
  if (!rows.length) return null;
  return <div className="flex flex-wrap items-center gap-1.5">{rows.map((r) => <Chip key={r.player_id} r={r} dim={dim} enter={r.effect === "none"} />)}</div>;
}

const FOOT_LINK = "inline-flex min-h-8 items-center gap-1 rounded-sm text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60";

// The story's foot, one line: the full story, "+N sources" (the rest of the reporting, each linked, by time, opened
// under the line) and "+k no change".
function Foot({ s, none, more, setMore }: { s: Story; none: number; more: boolean; setMore: (v: boolean) => void }) {
  const n = s.sources.length;
  if (!s.first?.url && !n && !none) return null;
  return (
    <Collapsible>
      <div className="flex flex-wrap items-center gap-x-4">
        {s.first?.url && (
          <a href={s.first.url} target="_blank" rel="noreferrer"
            className={`inline-flex min-h-8 items-center gap-1 font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-foreground/55 hover:text-foreground ${NAME_LINK}`}>
            Full story<ArrowUpRight aria-hidden className="size-3.5" />
          </a>
        )}
        {n > 0 && (
          <CollapsibleTrigger className={`group ${FOOT_LINK}`}>
            +{n} {n === 1 ? "source" : "sources"}
            <ChevronDown aria-hidden className="size-3.5 transition-transform duration-300 ease-(--ease-arrive) group-aria-expanded:rotate-180 motion-reduce:transition-none" />
          </CollapsibleTrigger>
        )}
        {none > 0 && (
          <button type="button" onClick={() => setMore(!more)} aria-expanded={more} className={FOOT_LINK}>
            {more ? "Hide no change" : `+${none} no change`}
          </button>
        )}
      </div>
      {n > 0 && (
        <CollapsibleContent>
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
      )}
    </Collapsible>
  );
}

// `focus`: the player page's player, whose row (the only one the API sends there) opens out into his line.
// `target`: the story a push opened, washed in ink and scrolled to by the feed.
export function NewsStory({ s, focus, target = false }: { s: Story; focus?: number; target?: boolean }) {
  const label = statusLabel(s.status);
  const retracted = s.status === "retracted";
  const { moved, none } = splitRows(s.players);
  const [more, setMore] = useState(false);
  return (
    <li id={`story-${s.id}`} data-target={target || undefined} className="news-story scroll-mt-16 space-y-2.5 px-3 py-4 sm:px-4">
      <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs leading-4 text-muted-foreground">
        {s.first && <span className="font-medium text-foreground/85">{s.first.source}</span>}
        {s.first && <span aria-hidden>·</span>}
        <Ago iso={s.first?.at ?? s.at} />
        {label && <Badge variant="muted" shape="chip" className="ml-1">{label}</Badge>}
        {!label && s.updated && <Badge variant="muted" shape="chip" className="ml-1">Updated</Badge>}
        {s.status === "corrected" && s.status_note && <span>by {s.status_note}</span>}
      </p>
      <div className="space-y-1">
        <h2 className={`max-w-[68ch] text-[0.9375rem] font-medium leading-snug text-balance ${retracted ? "text-foreground/60" : ""}`}>{s.headline}</h2>
        {s.overview && <p className="max-w-[68ch] text-sm leading-relaxed text-muted-foreground">{s.overview}</p>}
      </div>
      {focus != null
        ? s.players.filter((r) => r.player_id === focus).map((r) => <OpenRow key={r.player_id} r={r} dim={retracted} />)
        : <Chips rows={more ? [...moved, ...none] : moved} dim={retracted} />}
      <Foot s={s} none={focus != null ? 0 : none.length} more={more} setMore={setMore} />
    </li>
  );
}
