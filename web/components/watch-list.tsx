"use client";

import Link from "next/link";
import { Star } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { clientGet } from "@/app/api-client";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Item, ItemContent, ItemGroup } from "@/components/ui/item";
import { Spinner } from "@/components/ui/spinner";
import type { WatchChip, WatchPage, WatchRow } from "@/lib/api";
import { BOARD_CARD } from "@/lib/board";
import { minutesSince, shortAge, signed } from "@/lib/format";
import { NAME_LINK } from "@/lib/player-log";
import { useNow } from "./local-time";
import { Presence } from "./motion";
import { PlayerTrigger } from "./player-dialog";
import { TeamPanel } from "./player-media";
import { SectionHeading } from "./section-heading";
import { TeamLink } from "./team-link";
import { useWatch } from "./watch-context";
import { WatchStar } from "./watch-star";

// The watched players (GET /watch) in the API's three groups, each a board card of rows: the player on his club's
// colours, his name (opens the season modal), POS · TEAM (and the owner when he is on another roster), `add` as the
// Players table shows it, and the four signals the hourly watch pass looks for. A signal lit in the last 3 days is an
// amber chip with what it saw; the rest stand dim with the kind's name, so the row says what was checked as well as
// what fired. The rows follow the stars (useWatch), not this payload: a star tapped elsewhere shows here at once, and
// unwatching a row folds it away with an undo. A player just starred that /watch has not priced yet (the pool is
// rebuilt behind the answer) stands under JUST ADDED until a refetch brings him.

const KIND: Record<WatchChip["kind"], string> = { ahead: "Depth", usage: "Usage", news: "News", bench: "Bench" };

const GROUPS = [
  ["moving", "Moving", null],
  ["quiet", "Quiet", null],
  ["elsewhere", "Rostered elsewhere", "No pushes for these until he's dropped."],
] as const;

// how soon /watch is asked again while a starred player is still missing from it
const PENDING_POLL_MS = 8_000;

function Chip({ c }: { c: WatchChip }) {
  const now = useNow();
  if (!c.lit) {
    return <Badge variant="muted" shape="chip" className="bg-foreground/[0.06] text-foreground/55">{KIND[c.kind]}</Badge>;
  }
  // the label in the chip's caps, what it saw as a sentence beside it; a long one breaks inside the chip, never cut
  return (
    <Badge variant="close" shape="chip" title={c.at ? new Date(c.at).toLocaleString() : undefined}
      className="h-auto min-h-5 max-w-full items-baseline gap-x-1.5 py-0.5 whitespace-normal">
      <span>{KIND[c.kind]}</span>
      <span className="font-sans text-xs font-medium normal-case tracking-normal">{c.text}</span>
      {c.at && <span suppressHydrationWarning className="font-sans text-xs font-medium normal-case tracking-normal tabular-nums opacity-70">{shortAge(minutesSince(c.at, now))}</span>}
    </Badge>
  );
}

function Row({ p, onToggle }: { p: WatchRow; onToggle: (on: boolean) => void }) {
  const elsewhere = p.owner != null;
  const up = !elsewhere && (p.add ?? 0) > 0;
  return (
    // a grid: the tile spans the name and the chips; the chips run on under the number, so a lit one keeps its line
    <Item role="listitem" className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-x-3 gap-y-2 rounded-none border-0 px-3 py-3 sm:px-4">
      <PlayerTrigger p={p} className="row-span-2 block size-12 shrink-0 overflow-hidden rounded-md sm:size-14">
        <TeamPanel abbr={p.team} playerId={p.player_id} name={p.name} muted={elsewhere} tile />
      </PlayerTrigger>
      <ItemContent className="min-w-0 gap-0">
        <p className={`font-display text-[1.125rem] font-semibold leading-tight tracking-tight sm:text-[1.25rem] ${elsewhere ? "text-foreground/70" : ""}`}>
          <PlayerTrigger p={p} className={`rounded-sm ${NAME_LINK}`}>{p.name}</PlayerTrigger>
        </p>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-xs leading-4 text-muted-foreground">
          <span className="font-semibold text-foreground/80">{p.pos}</span>
          <span aria-hidden>·</span>
          <span>{p.team}</span>
          {p.owner && (
            // the owner takes his own line on the phone, where it would wrap anyway
            <span className="whitespace-nowrap max-sm:basis-full">
              <span aria-hidden className="mr-1.5 max-sm:hidden">·</span>
              <TeamLink id={p.owner.team_id} className="text-foreground/80">{p.owner.name}</TeamLink>
            </span>
          )}
        </p>
      </ItemContent>
      <div className="flex shrink-0 items-start gap-2.5 sm:gap-3">
        <div className="text-right">
          <p className={`font-display text-lg font-bold leading-none tracking-tight tabular-nums sm:text-[1.5rem] ${up ? "" : "text-foreground/70"}`}>
            {p.add == null ? "—" : signed(p.add)}
          </p>
          <p className="mt-1 font-display text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">add</p>
        </div>
        <WatchStar id={p.player_id} name={p.name} onToggle={onToggle} />
      </div>
      {/* what fired leads; the dim ones say what else was checked */}
      <div className="col-span-2 col-start-2 flex flex-wrap gap-1.5">
        {[...p.chips].sort((a, b) => Number(b.lit) - Number(a.lit)).map((c) => <Chip key={c.kind} c={c} />)}
      </div>
    </Item>
  );
}

// A starred player /watch does not list yet: the name the star was tapped with, a spinner where the tile goes.
function Pending({ id, name, onToggle }: { id: number; name: string | undefined; onToggle: (on: boolean) => void }) {
  return (
    <Item role="listitem" className="flex-nowrap items-center gap-3 rounded-none border-0 px-3 py-3 sm:px-4">
      <div className="flex size-12 shrink-0 items-center justify-center rounded-md bg-foreground/[0.08] text-muted-foreground sm:size-14">
        <Spinner aria-label="Pricing" />
      </div>
      <ItemContent className="min-w-0 gap-0">
        <p className="font-display text-[1.125rem] font-semibold leading-tight tracking-tight sm:text-[1.25rem]">{name ?? "New watch"}</p>
        <p className="mt-0.5 text-xs leading-4 text-muted-foreground">Pricing… he joins his group on the next pool build.</p>
      </ItemContent>
      <WatchStar id={id} name={name ?? "him"} onToggle={onToggle} />
    </Item>
  );
}

function Group({ title, note, count, children }: { title: string; note?: string | null; count: number; children: React.ReactNode }) {
  return (
    <Presence show={count > 0} fold>
      <section className="space-y-3">
        <div>
          <SectionHeading title={title} count={count} />
          {note && <p className="mt-1 text-xs text-muted-foreground">{note}</p>}
        </div>
        <Card className={BOARD_CARD}>
          <CardContent className="px-0 sm:px-0">
            <ItemGroup className="gap-0 divide-y divide-foreground/10">{children}</ItemGroup>
          </CardContent>
        </Card>
      </section>
    </Presence>
  );
}

export function WatchList({ initial }: { initial: WatchPage }) {
  const [data, setData] = useState(initial);
  const [seen, setSeen] = useState(initial);
  if (seen !== initial) {
    setSeen(initial);
    setData(initial);
  }
  const watch = useWatch();
  const all = [...data.groups.moving, ...data.groups.quiet, ...data.groups.elsewhere];
  // outside the provider (a test render) the payload's own rows stand
  const ids = watch?.ids ?? new Set(all.map((p) => p.player_id));
  const listed = new Set(all.map((p) => p.player_id));
  const pending = [...ids].filter((id) => !listed.has(id));
  const waiting = pending.join();

  // the undo runs after the tap's state has landed, so it reads the provider as it is then
  const latest = useRef(watch);
  useEffect(() => { latest.current = watch; }, [watch]);
  const toggled = (id: number, name: string) => (on: boolean) => {
    if (on) return;
    toast(`Stopped watching ${name}`, { action: { label: "Undo", onClick: () => void latest.current?.toggle(id, name) } });
  };

  useEffect(() => {
    if (!waiting) return;
    let current = true;
    const t = setTimeout(() => {
      clientGet<WatchPage>("/watch").then((d) => { if (current) setData(d); }).catch(() => {});
    }, PENDING_POLL_MS);
    return () => { current = false; clearTimeout(t); };
  }, [waiting, data]);

  if (ids.size === 0) {
    return (
      <Card className={BOARD_CARD}>
        <CardContent>
          <Empty className="py-10">
            <EmptyHeader>
              <EmptyMedia variant="icon" className="bg-foreground/[0.08]"><Star className="stroke-[2.25]" /></EmptyMedia>
              <EmptyTitle className="font-display text-xl font-semibold uppercase tracking-[0.14em]">Nobody watched</EmptyTitle>
              <EmptyDescription>
                Tap the star on a Players row, or on any player&apos;s card, to watch him. The watch pass looks at him every
                hour and pushes when he gets his chance.
              </EmptyDescription>
            </EmptyHeader>
            <EmptyContent>
              <Link href="/players" className={buttonVariants({ variant: "outline", size: "sm" })}>Open Players</Link>
            </EmptyContent>
          </Empty>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-7">
      <Group title="Just added" count={pending.length}>
        {pending.map((id) => <Pending key={id} id={id} name={watch?.names.get(id)} onToggle={toggled(id, watch?.names.get(id) ?? "him")} />)}
      </Group>
      {GROUPS.map(([key, title, note]) => {
        const rows = data.groups[key];
        return (
          <Group key={key} title={title} note={note} count={rows.filter((p) => ids.has(p.player_id)).length}>
            {rows.map((p) => (
              <Presence key={p.player_id} show={ids.has(p.player_id)} fold>
                <div><Row p={p} onToggle={toggled(p.player_id, p.name)} /></div>
              </Presence>
            ))}
          </Group>
        );
      })}
    </div>
  );
}
