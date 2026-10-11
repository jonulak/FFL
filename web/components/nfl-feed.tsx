"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useTransition, type CSSProperties } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { NflDrive, NflGame, NflGamePage, NflPlay } from "@/lib/api";
import { BOARD_CARD, LABEL } from "@/lib/board";
import { feedHref, filterDrives, gameHref, type FeedFilter } from "@/lib/nfl";
import { GAME_POLL_MS, NflPlayRow, NflScorebug } from "./nfl-game";
import { useLedger } from "./play-feed";
import { StaleBadge } from "./stale-badge";
import { useLive } from "./use-live";

const FILTERS: [FeedFilter, string][] = [["all", "All"], ["mine", "My players"], ["scoring", "Scoring"]];

// A drive's header: the club, ESPN's summary, and how it ended ("GB · 10 plays, 91 yards, 6:13 · TD").
// A drive still on the field has no result yet.
function DriveHeader({ d }: { d: NflDrive }) {
  return (
    <div className="flex items-baseline gap-2 bg-foreground/[0.04] px-(--card-spacing) py-2">
      <span className={`${LABEL} text-foreground`}>{d.team ?? "—"}</span>
      <span className="text-xs text-muted-foreground">{d.description}</span>
      {d.result && <span className={`${LABEL} ml-auto`}>{d.result}</span>}
    </div>
  );
}

// The play inside its arrival slot: a play that lands after the page opened opens its slot at the top and slides
// in (globals.css `.play-slot`), a scoring play leaving the lime wash (`data-gain`). Turnovers take an amber edge,
// penalties a muted FLAG chip. `entering` comes from ONE ledger over every play of the game (NflFeedLive): a
// ledger per drive would mount a new drive's first play as "still" — the kickoff or first snap he is waiting for —
// and a ledger over the filtered list would replay plays the filter merely hid.
function Plays({ plays, g, entering }: { plays: NflPlay[]; g: NflGame; entering: Set<string> }) {
  return (
    <div>
      {plays.map((p, i) => {
        const enter = entering.has(p.id);
        return (
          <div key={p.id} className="play-slot" data-side="me" data-enter={enter || undefined}
            data-gain={(enter && p.scoring) || undefined} style={enter ? { "--i": i } as CSSProperties : undefined}>
            <div className={`play-clip px-(--card-spacing) ${i > 0 ? "border-t border-foreground/10" : ""} ${p.turnover ? "shadow-[inset_3px_0_0_var(--close)]" : ""} ${p.scoring ? "bg-start/[0.07]" : ""}`}>
              {p.penalty && <Badge variant="muted" shape="chip" className="ml-[calc(3px+0.625rem)] mt-2">flag</Badge>}
              <NflPlayRow p={p} g={g} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function NflFeedLive({ initial, path, filter, club }: { initial: NflGamePage; path: string; filter: FeedFilter; club: string }) {
  const { data } = useLive(initial, path, { pollMs: GAME_POLL_MS });
  const router = useRouter();
  const [pending, start] = useTransition();
  // memoised: useLedger compares `items` by identity, so a fresh array every render would loop
  const all = useMemo(() => data.drives.flatMap((d) => d.plays), [data.drives]);
  const ledger = useLedger(all, (p) => p.id);
  const entering = new Set(ledger.filter((r) => r.state === "enter").map((r) => r.p.id));
  const drives = filterDrives(data.drives, filter);
  const total = data.drives.reduce((n, d) => n + d.plays.length, 0);
  const empty = data.game.status === "pre"
    ? "Plays appear here once the game starts."
    : filter === "mine" ? "None of your starters or your opponent's has been in a play yet."
    : filter === "scoring" ? "No points yet." : "No plays yet.";
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Link href={gameHref(data.week, club)} className={`${LABEL} hover:text-foreground`}>← Game</Link>
        <StaleBadge since={data.stale_since} />
      </div>
      <NflScorebug g={data.game} compact />
      <div className="flex items-center gap-3">
        <ToggleGroup value={[filter]} onValueChange={(v: string[]) => {
          const f = v[0] as FeedFilter | undefined;
          if (f && f !== filter) start(() => router.replace(feedHref(data.week, club, f), { scroll: false }));
        }} className={pending ? "opacity-60" : ""} aria-label="Filter plays">
          {FILTERS.map(([f, label]) => <ToggleGroupItem key={f} value={f} className="font-display text-sm font-semibold uppercase tracking-[0.1em]">{label}</ToggleGroupItem>)}
        </ToggleGroup>
        <span className="ml-auto font-display text-sm font-medium tabular-nums text-muted-foreground">{total} plays</span>
      </div>
      <Card className={`${BOARD_CARD} overflow-hidden`}>
        <CardContent className="px-0 py-0">
          {drives.length === 0
            ? <p className="py-6 text-center text-xs text-muted-foreground">{empty}</p>
            : drives.map((d) => <section key={d.id}><DriveHeader d={d} /><Plays plays={d.plays} g={data.game} entering={entering} /></section>)}
        </CardContent>
      </Card>
    </div>
  );
}
