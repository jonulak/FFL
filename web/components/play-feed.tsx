import Link from "next/link";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemTitle } from "@/components/ui/item";
import { Separator } from "@/components/ui/separator";
import type { Play } from "@/lib/api";
import { pts, signed } from "@/lib/format";
import { Clock } from "./local-time";
import { TeamPanel } from "./player-media";

// The play feed, on the board: a ledger of every points change for a starter in the matchup,
// newest first. It is the lineup row turned to face the play: the club tile, the name, the play
// text where the reason would be, and the delta as the big numeral with the running total
// beneath it, the way a posted score carries the projection it replaced. My rows hang from the
// left edge, the opponent's from the right, so the side is read from the column before the name.
// `compact` is the Matchup strip's one-line form: name and play on a single truncated line, the
// game clock and the delta at the inner end, no running total (that is the /plays page's job).
export function PlayFeed({ plays, empty = "No plays yet.", compact = false }: { plays: Play[]; empty?: string; compact?: boolean }) {
  if (plays.length === 0) {
    return <p className="py-4 text-center text-xs text-muted-foreground">{empty}</p>;
  }
  return (
    <ItemGroup className="gap-0 divide-y divide-foreground/10">
      {plays.map((p) => compact
        ? <CompactRow key={`${p.at}-${p.player_id}-${p.total}`} p={p} />
        : <PlayRow key={`${p.at}-${p.player_id}-${p.total}`} p={p} />)}
    </ItemGroup>
  );
}

const UNIT = "font-display text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground";

// A row is mirrored, not merely aligned, for the opponent: the Item is reversed so tile and
// numeral swap ends and the two sides read as two teams facing each other across the same ledger.
function PlayRow({ p }: { p: Play }) {
  const mine = p.side === "me";
  const loss = p.delta < 0;
  return (
    <div role="listitem" className={`flex py-2.5 ${mine ? "justify-start" : "justify-end"}`}>
      <Item size="sm" className={`w-full max-w-[88%] flex-nowrap gap-2.5 rounded-none border-0 p-0 sm:max-w-[74%] sm:gap-3 ${mine ? "" : "flex-row-reverse"}`}>
        <ItemMedia className="size-10 overflow-hidden rounded-md sm:size-12 *:w-full">
          <TeamPanel abbr={p.team} playerId={p.player_id} name={p.name} muted={false} tile />
        </ItemMedia>
        <ItemContent className={`min-w-0 gap-0 ${mine ? "" : "items-end text-right"}`}>
          <ItemTitle className={`line-clamp-none flex! max-w-full flex-wrap items-baseline gap-x-2 gap-y-0.5 ${mine ? "" : "flex-row-reverse"}`}>
            <span className="truncate font-display text-[1.125rem] font-semibold leading-none tracking-tight">{p.name}</span>
            <span className="text-xs font-normal text-muted-foreground">
              <span className="font-semibold text-foreground/80">{p.pos}</span> <span aria-hidden>·</span> {p.team}
            </span>
          </ItemTitle>
          {p.text && <ItemDescription className={`mt-1 line-clamp-none text-[13px] leading-snug text-foreground/85 ${mine ? "" : "text-right"}`}>{p.text}</ItemDescription>}
          <ItemDescription className={`mt-0.5 line-clamp-none text-xs leading-normal tabular-nums ${mine ? "" : "text-right"}`}>
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

// The strip's row: one line. The tile is a small square of club colour, the name leads and the
// play text runs on after it, truncating; the quarter and clock (from `sm`) and the delta close
// the line at the spine. The full row, with the stamp and the running total, is on /plays.
function CompactRow({ p }: { p: Play }) {
  const mine = p.side === "me";
  const loss = p.delta < 0;
  return (
    <div role="listitem" className={`flex py-1.5 ${mine ? "justify-start" : "justify-end"}`}>
      <Item size="xs" className={`w-full max-w-[94%] flex-nowrap gap-2 rounded-none border-0 p-0 sm:max-w-[82%] sm:gap-2.5 ${mine ? "" : "flex-row-reverse"}`}>
        <ItemMedia className="size-7 overflow-hidden rounded-sm *:w-full">
          <TeamPanel abbr={p.team} playerId={p.player_id} name={p.name} muted={false} tile />
        </ItemMedia>
        <ItemContent className={`min-w-0 gap-0 ${mine ? "" : "items-end text-right"}`}>
          <ItemTitle className={`line-clamp-none block w-full max-w-full truncate leading-tight ${mine ? "" : "text-right"}`} title={p.text ?? undefined}>
            <span className="font-display text-[1.0625rem] font-semibold tracking-tight">{p.name}</span>
            {p.text && <span className="ml-2 text-[13px] font-normal text-foreground/85">{p.text}</span>}
          </ItemTitle>
        </ItemContent>
        <ItemActions className={`gap-2.5 whitespace-nowrap tabular-nums ${mine ? "" : "flex-row-reverse"}`}>
          {p.quarter != null && p.clock && <span className="hidden text-xs text-muted-foreground sm:inline">Q{p.quarter} {p.clock}</span>}
          <span className="flex items-baseline gap-1.5">
            <span className={`font-display text-[1.25rem] font-bold leading-none tracking-tight ${loss ? "text-foreground/70" : ""}`}>{signed(p.delta)}</span>
            <span className={UNIT}>pts</span>
          </span>
        </ItemActions>
      </Item>
    </div>
  );
}

// The board card the feed sits in: a section heading with the hairline running out to the
// right and, at the end of the line, either the way to the whole week or the row count.
export function PlayFeedCard({ plays, title, count, link, empty, compact }: {
  plays: Play[]; title: string; count?: number; link?: { href: string; label: string }; empty?: string; compact?: boolean;
}) {
  return (
    <Card className="rounded-lg py-0 shadow-[0_10px_28px_-16px_rgb(0_0_0_/_0.55)] [--card-spacing:--spacing(3)] sm:[--card-spacing:--spacing(4)]" aria-label={title}>
      <CardHeader className={`flex flex-row items-baseline gap-3 ${compact ? "pt-2.5 sm:pt-3" : "pt-3 sm:pt-3.5"}`}>
        <h2 className="shrink-0 font-display text-xl font-semibold uppercase tracking-[0.14em]">{title}</h2>
        {count != null && <span className="font-display text-xl font-medium text-muted-foreground tabular-nums">{count}</span>}
        <Separator className="mb-1 flex-1 self-end bg-foreground/12" />
        {link && (
          <Link href={link.href} className="shrink-0 font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-foreground/55 hover:text-foreground">
            {link.label} <span aria-hidden>→</span>
          </Link>
        )}
      </CardHeader>
      <CardContent className={compact ? "-mt-2 pb-1" : "-mt-1 pb-1"}>
        <PlayFeed plays={plays} empty={empty} compact={compact} />
      </CardContent>
    </Card>
  );
}

// The strip on the Matchup page: the latest few rows, one line each, and the way to the whole day.
export function PlayFeedStrip({ plays }: { plays: Play[] }) {
  return <PlayFeedCard plays={plays} title="Latest plays" link={{ href: "/plays", label: "All plays" }} compact />;
}
