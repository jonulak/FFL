import Link from "next/link";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
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
export function PlayFeed({ plays, empty = "No plays yet." }: { plays: Play[]; empty?: string }) {
  if (plays.length === 0) {
    return <p className="py-4 text-center text-xs text-muted-foreground">{empty}</p>;
  }
  return (
    <ol className="divide-y divide-foreground/10">
      {plays.map((p) => <PlayRow key={`${p.at}-${p.player_id}-${p.total}`} p={p} />)}
    </ol>
  );
}

function PlayRow({ p }: { p: Play }) {
  const mine = p.side === "me";
  const loss = p.delta < 0;
  return (
    // A row is mirrored, not merely aligned, for the opponent: tile and numeral swap ends so
    // the two sides read as two teams facing each other across the same ledger.
    <li className={`flex py-2.5 ${mine ? "justify-start" : "justify-end"}`}>
      <div className={`grid w-full max-w-[88%] items-center gap-x-2.5 sm:max-w-[74%] sm:gap-x-3 ${mine ? "grid-cols-[2.5rem_minmax(0,1fr)_auto] text-left sm:grid-cols-[3rem_minmax(0,1fr)_auto]" : "grid-cols-[auto_minmax(0,1fr)_2.5rem] text-right sm:grid-cols-[auto_minmax(0,1fr)_3rem]"}`}>
        <div className={`aspect-square overflow-hidden rounded-md ${mine ? "" : "order-3"}`}>
          <TeamPanel abbr={p.team} playerId={p.player_id} name={p.name} muted={false} tile />
        </div>
        <div className={`min-w-0 ${mine ? "" : "order-2"}`}>
          <p className={`flex flex-wrap items-baseline gap-x-2 gap-y-0.5 ${mine ? "" : "flex-row-reverse"}`}>
            <span className="truncate font-display text-[1.125rem] font-semibold leading-none tracking-tight">{p.name}</span>
            <span className="text-xs text-muted-foreground">
              <span className="font-semibold text-foreground/80">{p.pos}</span> <span aria-hidden>·</span> {p.team}
            </span>
          </p>
          {p.text && <p className="mt-1 text-[13px] leading-snug text-foreground/85">{p.text}</p>}
          <p className="mt-0.5 text-xs tabular-nums text-muted-foreground">
            <Clock iso={p.at} />
            {p.quarter != null && p.clock && <> <span aria-hidden>·</span> Q{p.quarter} {p.clock}</>}
          </p>
        </div>
        <div className={`tabular-nums ${mine ? "text-right" : "order-1 text-left"}`}>
          <p className={`flex items-baseline gap-1.5 ${mine ? "justify-end" : ""}`}>
            <span className={`font-display text-[1.5rem] font-bold leading-none tracking-tight ${loss ? "text-foreground/70" : ""}`}>{signed(p.delta)}</span>
            <span className="font-display text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">pts</span>
          </p>
          <p className={`mt-0.5 text-xs text-muted-foreground ${mine ? "text-right" : "text-left"}`}>now {pts(p.total)}</p>
        </div>
      </div>
    </li>
  );
}

// The board card the feed sits in: a section heading with the hairline running out to the
// right and, at the end of the line, either the way to the whole week or the row count.
export function PlayFeedCard({ plays, title, count, link, empty }: {
  plays: Play[]; title: string; count?: number; link?: { href: string; label: string }; empty?: string;
}) {
  return (
    <Card className="rounded-lg py-0 shadow-[0_10px_28px_-16px_rgb(0_0_0_/_0.55)] [--card-spacing:--spacing(3)] sm:[--card-spacing:--spacing(4)]" aria-label={title}>
      <CardHeader className="flex flex-row items-baseline gap-3 pt-3 sm:pt-3.5">
        <h2 className="shrink-0 font-display text-xl font-semibold uppercase tracking-[0.14em]">{title}</h2>
        {count != null && <span className="font-display text-xl font-medium text-muted-foreground tabular-nums">{count}</span>}
        <Separator className="mb-1 flex-1 self-end bg-foreground/12" />
        {link && (
          <Link href={link.href} className="shrink-0 font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-foreground/55 hover:text-foreground">
            {link.label} <span aria-hidden>→</span>
          </Link>
        )}
      </CardHeader>
      <CardContent className="-mt-1 pb-1">
        <PlayFeed plays={plays} empty={empty} />
      </CardContent>
    </Card>
  );
}

// The strip on the Matchup page: the latest few rows and the way to the whole day.
export function PlayFeedStrip({ plays }: { plays: Play[] }) {
  return <PlayFeedCard plays={plays} title="Latest plays" link={{ href: "/plays", label: "All plays" }} />;
}
