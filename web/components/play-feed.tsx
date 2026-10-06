"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemTitle } from "@/components/ui/item";
import { Separator } from "@/components/ui/separator";
import type { Play } from "@/lib/api";
import { pts, signed } from "@/lib/format";
import { Clock } from "./local-time";
import { NAME_LINK } from "@/lib/player-log";
import { PlayerTrigger } from "./player-dialog";
import { TeamPanel } from "./player-media";

// The play feed, on the board: a ledger of every points change for a starter in the matchup,
// newest first. It is the lineup row turned to face the play: the club tile, the name, the play
// text where the reason would be, and the delta as the big numeral with the running total
// beneath it, the way a posted score carries the projection it replaced. My rows hang from the
// left edge, the opponent's from the right, so the side is read from the column before the name.
// `compact` is the Matchup strip's one-line form: name and play on a single truncated line, the
// game clock and the delta at the inner end, no running total (that is the /plays page's job).
export function PlayFeed({ plays, empty = "No plays yet.", compact = false, pick }: { plays: Play[]; empty?: string; compact?: boolean; pick?: Pick }) {
  const rows = useLedger(plays);
  if (rows.length === 0) {
    return <p className="py-4 text-center text-xs text-muted-foreground">{empty}</p>;
  }
  const Row = compact ? CompactRow : PlayRow;
  // The hairline and the gap between rows live inside each slot's clip, so they open and fold
  // with it: below every row but the last that stays, above a row on its way out. Drawn by the
  // group (divide-y, gap) they would step 9px the moment a row came or went.
  const sep = <div aria-hidden className={`border-b border-foreground/10 ${compact ? "pt-2" : "pt-2.5"}`} />;
  const last = plays.length - 1;
  // the nth row in pairs with the nth row out, so both carry their own count
  let fresh = 0;
  let gone = 0;
  return (
    <ItemGroup className="gap-0 has-data-[size=sm]:gap-0 has-data-[size=xs]:gap-0">
      {rows.map(({ p, state }, i) => (
        // the slot is the list item; its inner clip is what the opening and folding animate
        <div key={key(p)} role={state === "leave" ? undefined : "listitem"} aria-hidden={state === "leave" || undefined}
          inert={state === "leave"} className="play-slot" data-side={p.side === "me" ? "me" : "opp"}
          data-enter={state === "enter" || undefined} data-leave={state === "leave" || undefined}
          data-gain={p.delta > 0 || undefined} style={state === "still" ? undefined : { "--i": state === "enter" ? fresh++ : gone++ } as React.CSSProperties}>
          <div className="play-clip">
            {state === "leave" && sep}
            <Row p={p} pick={pick} />
            {state !== "leave" && i < last && sep}
          </div>
        </div>
      ))}
    </ItemGroup>
  );
}

const key = (p: Play) => `${p.at}-${p.player_id}-${p.total}`;
// play-close's 380ms plus slack; a batch of several staggers past it, and a cut-short fold of
// the last one is the cost of not tracking animationend per row
const LEAVE_MS = 450;

type Ledger = { p: Play; state: "still" | "enter" | "leave" }[];

// What the feed draws: the rows on screen when it mounted stay still, any row that arrives after
// is marked to enter (and keeps the mark, so a re-render never replays it), and a row that drops
// off the end (the strip shows the latest five) is held for one fold before it goes.
function useLedger(plays: Play[]): Ledger {
  const [first] = useState(() => new Set(plays.map(key)));
  const [prev, setPrev] = useState(plays);
  const [leaving, setLeaving] = useState<Play[]>([]);
  if (prev !== plays) {
    const now = new Set(plays.map(key));
    const gone = prev.filter((p) => !now.has(key(p)));
    setPrev(plays);
    if (gone.length > 0 || leaving.some((p) => now.has(key(p)))) {
      setLeaving([...leaving.filter((p) => !now.has(key(p))), ...gone]);
    }
  }
  useEffect(() => {
    if (leaving.length === 0) return;
    const t = setTimeout(() => setLeaving([]), LEAVE_MS);
    return () => clearTimeout(t);
  }, [leaving]);
  return [
    ...plays.map((p) => ({ p, state: first.has(key(p)) ? "still" as const : "enter" as const })),
    // the dropped rows are the oldest, so they fold at the foot of the ledger
    ...leaving.map((p) => ({ p, state: "leave" as const })),
  ];
}

const UNIT = "font-display text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground";

// A play row opens the player's season modal from its tile and name, with what the row knows; a
// matchup view's strip opens its breakdown instead (`pick`, PlayerTrigger).
type Pick = (id: number) => boolean;
const seed = (p: Play) => ({ player_id: p.player_id, name: p.name, pos: p.pos, team: p.team });

// A row is mirrored, not merely aligned, for the opponent: the Item is reversed so tile and
// numeral swap ends and the two sides read as two teams facing each other across the same ledger.
function PlayRow({ p }: { p: Play; pick?: Pick }) {
  const mine = p.side === "me";
  const loss = p.delta < 0;
  return (
    <div className={`play-row flex py-2.5 ${mine ? "justify-start" : "justify-end"}`}>
      <Item size="sm" className={`w-full max-w-[88%] flex-nowrap gap-2.5 rounded-none border-0 p-0 sm:max-w-[74%] sm:gap-3 ${mine ? "" : "flex-row-reverse"}`}>
        <ItemMedia className="size-10 overflow-hidden rounded-md sm:size-12 *:w-full">
          <PlayerTrigger p={seed(p)} className="block size-full">
            <TeamPanel abbr={p.team} playerId={p.player_id} name={p.name} muted={false} tile />
          </PlayerTrigger>
        </ItemMedia>
        <ItemContent className={`min-w-0 gap-0 ${mine ? "" : "items-end text-right"}`}>
          <ItemTitle className={`line-clamp-none flex! max-w-full flex-wrap items-baseline gap-x-2 gap-y-0.5 ${mine ? "" : "flex-row-reverse"}`}>
            <PlayerTrigger p={seed(p)} className={`-mb-[0.2em] truncate rounded-sm pb-[0.2em] font-display text-[1.125rem] font-semibold leading-none tracking-tight ${NAME_LINK}`}>{p.name}</PlayerTrigger>
            <span className="text-xs font-normal text-muted-foreground">
              <span className="font-semibold text-foreground/80">{p.pos}</span> <span aria-hidden>·</span> {p.team}
            </span>
          </ItemTitle>
          {p.text && <ItemDescription className={`mt-1 line-clamp-none text-[0.8125rem] leading-snug text-foreground/85 ${mine ? "" : "text-right"}`}>{p.text}</ItemDescription>}
          <ItemDescription className={`mt-0.5 line-clamp-none text-xs leading-4 tabular-nums ${mine ? "" : "text-right"}`}>
            <Clock iso={p.at} />
            {p.quarter != null && p.clock && <> <span aria-hidden>·</span> Q{p.quarter} {p.clock}</>}
          </ItemDescription>
        </ItemContent>
        <ItemActions className={`flex-col gap-0 self-center tabular-nums ${mine ? "items-end text-right" : "items-start text-left"}`}>
          <p className="flex items-baseline gap-1.5">
            <span className={`font-display text-[1.5rem] font-bold leading-none tracking-tight ${loss ? "text-foreground/70" : ""}`}>{signed(p.delta)}</span>
            <span className={UNIT}>pts</span>
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">now {pts(p.total)}</p>
        </ItemActions>
      </Item>
    </div>
  );
}

// The strip's row: one line. The tile is a small square of club colour, the name leads, and the
// quarter and clock and the delta close the line at the spine. The play text itself belongs to
// the /plays page, with the stamp and the running total; the strip says who moved, when and by
// how much, and the full text is the row's title. A truncated name at leading-none clips its
// descenders, so its box is padded below and the margin pulls the line back (both rows' names).
function CompactRow({ p, pick }: { p: Play; pick?: Pick }) {
  const mine = p.side === "me";
  const loss = p.delta < 0;
  return (
    <div className={`play-row flex py-1.5 ${mine ? "justify-start" : "justify-end"}`}>
      <Item size="xs" className={`w-full max-w-[85%] flex-nowrap gap-2 rounded-none border-0 p-0 sm:max-w-[82%] sm:gap-2.5 ${mine ? "" : "flex-row-reverse"}`} title={p.text ?? undefined}>
        <ItemMedia className="size-7 overflow-hidden rounded-sm *:w-full">
          <PlayerTrigger p={seed(p)} pick={pick} className="block size-full">
            <TeamPanel abbr={p.team} playerId={p.player_id} name={p.name} muted={false} tile />
          </PlayerTrigger>
        </ItemMedia>
        <ItemContent className={`min-w-0 gap-0 ${mine ? "" : "items-end text-right"}`}>
          <ItemTitle className={`line-clamp-none block w-full max-w-full -mb-[0.2em] truncate pb-[0.2em] font-display text-[1.125rem] font-semibold leading-none tracking-tight ${mine ? "" : "text-right"}`}>
            <PlayerTrigger p={seed(p)} pick={pick} className={`rounded-sm ${NAME_LINK}`}>{p.name}</PlayerTrigger>
          </ItemTitle>
        </ItemContent>
        <ItemActions className={`gap-2.5 whitespace-nowrap tabular-nums ${mine ? "" : "flex-row-reverse"}`}>
          {p.quarter != null && p.clock && <span className="text-xs text-muted-foreground">Q{p.quarter} {p.clock}</span>}
          {/* the delta with the running total beneath it, the way a posted score carries the projection it replaced */}
          <span className={`flex flex-col ${mine ? "items-end" : "items-start"}`}>
            <span className="flex items-baseline gap-1.5">
              <span className={`font-display text-[1.25rem] font-bold leading-none tracking-tight ${loss ? "text-foreground/70" : ""}`}>{signed(p.delta)}</span>
              <span className={UNIT}>pts</span>
            </span>
            <span className="mt-0.5 text-xs leading-4 text-muted-foreground">now {pts(p.total)}</span>
          </span>
        </ItemActions>
      </Item>
    </div>
  );
}

// The board card the feed sits in: a section heading with the hairline running out to the
// right and, at the end of the line, either the way to the whole week or the row count.
export function PlayFeedCard({ plays, title, count, link, empty, compact, pick }: {
  plays: Play[]; title: string; count?: number; link?: { href: string; label: string }; empty?: string; compact?: boolean; pick?: Pick;
}) {
  return (
    <Card className="rounded-lg py-0 shadow-[0_10px_28px_-16px_rgb(0_0_0_/_0.55)] [--card-spacing:--spacing(3)] sm:[--card-spacing:--spacing(4)]" aria-label={title}>
      <CardHeader className={`flex flex-row items-baseline gap-3 ${compact ? "pt-2.5 sm:pt-3" : "pt-3 sm:pt-3.5"}`}>
        <h2 className="shrink-0 font-display text-xl font-semibold uppercase tracking-[0.14em]">{title}</h2>
        {count != null && <span className="font-display text-xl font-medium text-muted-foreground tabular-nums">{count}</span>}
        <Separator className="mb-1 flex-1 self-end bg-foreground/12" />
        {link && (
          <Link href={link.href} className="-my-2 shrink-0 py-2 font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-foreground/55 hover:text-foreground dark:text-foreground/65 dark:hover:text-foreground">
            {link.label} <span aria-hidden>→</span>
          </Link>
        )}
      </CardHeader>
      <CardContent className={compact ? "-mt-2 pb-1" : "-mt-1 pb-1"}>
        <PlayFeed plays={plays} empty={empty} compact={compact} pick={pick} />
      </CardContent>
    </Card>
  );
}

// The strip on every Matchup board: the latest few rows, one line each, and the way to that
// game's whole day (`href`, lib/matchup-link.ts playsHref). A row's tile and name open the
// board's own breakdown through `pick` (PlayerTrigger), as a tap on the board does.
export function PlayFeedStrip({ plays, href, pick }: { plays: Play[]; href: string; pick?: Pick }) {
  return <PlayFeedCard plays={plays} title="Latest plays" link={{ href, label: "All plays" }} compact pick={pick} />;
}
