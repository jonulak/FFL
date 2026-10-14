"use client";

import { useRouter } from "next/navigation";
import { useMemo, useOptimistic, useState, useTransition, ViewTransition, type CSSProperties } from "react";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { Card, CardContent } from "@/components/ui/card";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { NflDrive, NflGame, NflGamePage, NflPlay } from "@/lib/api";
import { BOARD_CARD, LABEL } from "@/lib/board";
import { feedHref, filterDrives, priorScores, slideIns, type FeedFilter, type FeedSeen } from "@/lib/nfl";
import { KickoffTime } from "./local-time";
import { NflPlayRow } from "./nfl-game";
import { useLedger } from "./play-feed";
import { SectionHeading } from "./section-heading";
import { Presence } from "./motion";

const FILTERS: [FeedFilter, string][] = [["all", "All"], ["mine", "My players"], ["scoring", "Scoring"]];

// A drive's header, the accordion's trigger: the club, ESPN's summary, and how it ended ("GB · 10 plays, 91 yards,
// 6:13 · TD"), the chevron after. A drive still on the field has no result yet.
function DriveHeader({ d }: { d: NflDrive }) {
  return (
    <AccordionTrigger className="items-center gap-2 rounded-none border-0 bg-foreground/[0.04] px-(--card-spacing) py-2.5 hover:bg-foreground/[0.07] hover:no-underline focus-visible:ring-inset">
      <span className={`${LABEL} text-foreground`}>{d.team ?? "—"}</span>
      <span className="min-w-0 flex-1 text-xs font-normal text-muted-foreground">{d.description}</span>
      <Presence show={d.result != null}><span className={`${LABEL} shrink-0`}>{d.result}</span></Presence>
    </AccordionTrigger>
  );
}

// The play inside its arrival slot: a play that lands after the page opened opens its slot at the top and slides
// in (globals.css `.play-slot`), a scoring play leaving the lime wash (`data-gain`). Turnovers and penalties are chips
// inside the row (NflPlayRow). `entering` comes from ONE ledger over every play of the game (NflFeedLive): a
// ledger per drive would mount a new drive's first play as "still" — the kickoff or first snap he is waiting for —
// and a ledger over the filtered list would replay plays the filter merely hid.
function Plays({ plays, g, entering, prior }: { plays: NflPlay[]; g: NflGame; entering: Set<string>; prior: ReturnType<typeof priorScores> }) {
  return (
    <div>
      {plays.map((p, i) => {
        const enter = entering.has(p.id);
        return (
          <div key={p.id} className="play-slot" data-side="me" data-enter={enter || undefined}
            data-gain={(enter && p.scoring) || undefined} style={enter ? { "--i": i } as CSSProperties : undefined}>
            <div className={`play-clip px-(--card-spacing) ${i > 0 ? "border-t border-foreground/10" : ""} ${p.scoring ? "bg-start/[0.07]" : ""}`}>
              <NflPlayRow p={p} g={g} prev={prior[p.id]} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

// Every play of the game under its drives, newest first, on the game page beneath "In this game". `data` is the
// page's live payload (NflGameLive polls it); `filter` is the page's `?f=` (URL state, so back and reload keep it).
export function NflPlayFeed({ data, filter, club }: { data: NflGamePage; filter: FeedFilter; club: string }) {
  const router = useRouter();
  const [pending, start] = useTransition();
  // the toggle pressed takes at once; the page's own filter takes over when it lands
  const [shown, setShown] = useOptimistic(filter);
  // memoised: useLedger compares `items` by identity, so a fresh array every render would loop
  const all = useMemo(() => data.drives.flatMap((d) => d.plays), [data.drives]);
  const ledger = useLedger(all, (p) => p.id);
  const drives = filterDrives(data.drives, filter);
  const prior = useMemo(() => priorScores(all), [all]);
  // Every drive starts collapsed but the one on the field (no result yet, game on). When the next drive takes the
  // field it opens in the finished one's place; drives opened by hand stay open through polls and filters.
  const live = data.game.status === "in" ? data.drives.find((d) => !d.result)?.id ?? null : null;
  const [stored, setOpen] = useState<{ ids: string[]; live: string | null }>({ ids: live ? [live] : [], live });
  // derived state, set in render like useLedger's; the rest of this render reads the new value, or `seen` below
  // would record a play of the drive just folded as shown and it would never slide in when opened
  const open = live === stored.live ? stored : { ids: [...stored.ids.filter((id) => id !== stored.live), ...(live ? [live] : [])], live };
  if (open !== stored) setOpen(open);
  // A row slides in once per page life, the first time it is on screen (lib/nfl slideIns): the ledger keeps saying
  // "enter" for a play that arrived after the page opened, and a filter or a folded drive that hides the row and
  // shows it again remounts it, which would replay the animation.
  const [seen, setSeen] = useState<FeedSeen>({ shown: new Set(), animated: new Set() });
  const arrived = new Set(ledger.filter((r) => r.state === "enter").map((r) => r.p.id));
  const onScreen = drives.filter((d) => open.ids.includes(d.id)).flatMap((d) => d.plays.map((p) => p.id));
  const { entering, next } = slideIns(seen, arrived, onScreen);
  if (next) setSeen(next);  // derived state, set in render like `open`
  const total = data.drives.reduce((n, d) => n + d.plays.length, 0);
  const empty = data.game.status === "pre"
    ? <>Kickoff <KickoffTime iso={data.game.kickoff} />. Plays appear here once the game starts.</>
    : filter === "mine" ? (data.marked !== false ? "None of your starters has been in a play yet." : "Can't pick out your starters: the Matchup board didn't load.")
    : filter === "scoring" ? "No points yet." : "No plays yet.";
  return (
    <section className="space-y-2">
      <SectionHeading title="Plays" count={total} />
      <ToggleGroup value={[shown]} onValueChange={(v: string[]) => {
        const f = v[0] as FeedFilter | undefined;
        if (f && f !== filter) start(() => {
          setShown(f);
          router.replace(feedHref(data.week, club, f), { scroll: false });
        });
      }} aria-label="Filter plays">
        {FILTERS.map(([f, label]) => <ToggleGroupItem key={f} value={f} className="font-display text-sm font-semibold uppercase tracking-[0.1em]">{label}</ToggleGroupItem>)}
      </ToggleGroup>
      {/* the list steps back while the filter is fetched, then the new one crossfades in over it */}
      <Card data-pending={pending || undefined} className={`${BOARD_CARD} pending-dim overflow-hidden`}>
        <ViewTransition update="swap" default="none">
        <CardContent className="px-0 py-0">
          {drives.length === 0
            ? <p className="py-6 text-center text-xs text-muted-foreground">{empty}</p>
            : (
              <Accordion multiple value={open.ids} onValueChange={(ids: string[]) => setOpen({ ...open, ids })}>
                {drives.map((d) => (
                  <AccordionItem key={d.id} value={d.id} className="border-foreground/10">
                    <DriveHeader d={d} />
                    <AccordionContent className="pb-0"><Plays plays={d.plays} g={data.game} entering={entering} prior={prior} /></AccordionContent>
                  </AccordionItem>
                ))}
              </Accordion>
            )}
        </CardContent>
        </ViewTransition>
      </Card>
    </section>
  );
}
