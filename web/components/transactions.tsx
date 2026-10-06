"use client";

import { ArrowLeftRight, Minus, Plus, UserRound } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Toggle } from "@/components/ui/toggle";
import type { Transactions, TxMove, TxPlayer, TxSide } from "@/lib/api";
import { BOARD_CARD, LABEL } from "@/lib/board";
import { NAME_LINK } from "@/lib/player-log";
import { clubLogoSrc, headshotUrl } from "@/lib/teams";
import { byWeek, filterMoves, KIND_LABEL, pairs, stamp, TX_FILTERS, weekLabel, type TxFilter } from "@/lib/transactions";
import { ClubLogo } from "./club-logo";
import { PlayerTrigger } from "./player-dialog";
import { TeamLogo, TeamPanel } from "./player-media";
import { SectionHeading } from "./section-heading";
import { StaleBadge } from "./stale-badge";
import { TeamLink } from "./team-link";

// The league's wire, on the board: every move a week's ledger, newest first. An add/drop move is
// one line, the team at the left, then what came in beside what went out, so the pair reads
// across like a box-score line; from `sm` the card's header names the two columns. A trade is the
// two clubs facing each other across a swap mark, each over what it received, the matchup
// board's mirrored halves. What left a roster is stepped back: its tile desaturated like an idle
// player's, its name at 70% ink. No colour says add or drop; a glyph and the column do (the
// Three Verdicts Rule keeps lime, amber and red for the call).

/* eslint-disable @next/next/no-img-element */

// The tile: his portrait on his club's colours (TeamPanel). A player ESPN gives no club (out of
// the NFL, or the lookup failed) has no colours to stand on, so he stands on a plain ink tile.
function Tile({ p, out }: { p: TxPlayer; out: boolean }) {
  const [gone, setGone] = useState(false);
  if (p.team) return <TeamPanel abbr={p.team} playerId={p.player_id} name={p.name} muted={out} tile />;
  const src = headshotUrl(p.player_id);
  return (
    <div aria-hidden className={`relative flex h-full items-end justify-center overflow-hidden bg-foreground/10 ${out ? "opacity-80" : ""}`}>
      {src && !gone ? (
        <img src={src} alt="" loading="lazy" decoding="async" className="w-[128%] max-w-none grayscale-[.4]" onError={() => setGone(true)} />
      ) : (
        <UserRound className="mb-1.5 size-6 text-foreground/40" />
      )}
    </div>
  );
}

function PlayerLine({ p, out, mirror = false }: { p: TxPlayer; out: boolean; mirror?: boolean }) {
  const seed = { player_id: p.player_id, name: p.name, pos: p.pos ?? "", team: p.team ?? "" };
  const Glyph = out ? Minus : Plus;
  return (
    <div className={`flex min-w-0 items-center gap-2.5 ${mirror ? "flex-row-reverse text-right" : ""}`}>
      <span className={`grid size-5 shrink-0 place-items-center rounded-sm ${out ? "bg-foreground/6 text-foreground/55" : "bg-foreground/12 text-foreground"}`}>
        <Glyph className="size-3.5" strokeWidth={2.5} aria-hidden />
        <span className="sr-only">{out ? "dropped" : "added"}</span>
      </span>
      <PlayerTrigger p={seed} className="block size-10 shrink-0 overflow-hidden rounded-md sm:size-11">
        <Tile p={p} out={out} />
      </PlayerTrigger>
      <div className="min-w-0">
        <p className={`truncate font-display text-[1.0625rem] font-semibold leading-tight tracking-tight sm:text-[1.125rem] ${out ? "text-foreground/70" : ""}`}>
          <PlayerTrigger p={seed} className={`rounded-sm ${NAME_LINK}`}>{p.name}</PlayerTrigger>
        </p>
        {/* mirrored, the line only moves to the right edge: it still reads POS · TEAM */}
        <p className={`mt-0.5 flex items-center gap-1.5 text-xs leading-4 text-muted-foreground ${mirror ? "justify-end" : ""}`}>
          {p.pos && <span className="font-semibold text-foreground/80">{p.pos}</span>}
          {p.pos && p.team && <span aria-hidden>·</span>}
          {p.team && <span className="flex items-center gap-1"><TeamLogo abbr={p.team} size={14} />{p.team}</span>}
          {!p.pos && !p.team && <span>no club</span>}
        </p>
      </div>
    </div>
  );
}

// The fantasy team: its mark and name (opens its roster), with the ME chip on mine.
function Club({ s, me, size = 36, mirror = false, children }: { s: TxSide; me: boolean; size?: number; mirror?: boolean; children?: React.ReactNode }) {
  return (
    <div className={`flex min-w-0 items-center gap-2.5 ${mirror ? "flex-row-reverse text-right" : ""}`}>
      <ClubLogo src={clubLogoSrc(s)} abbrev={s.abbrev || s.team} size={size} className="shrink-0" />
      <div className="min-w-0">
        <p className={`flex items-center gap-2 font-display text-[1.125rem] font-semibold leading-tight tracking-tight sm:text-[1.1875rem] ${mirror ? "flex-row-reverse" : ""}`}>
          <TeamLink id={s.team_id} className="min-w-0 truncate">{s.team}</TeamLink>
          {me && <Badge variant="muted" shape="chip" className="shrink-0">me</Badge>}
        </p>
        {children}
      </div>
    </div>
  );
}

const LINE = "grid gap-x-6 gap-y-3 px-3 py-3.5 sm:grid-cols-[minmax(0,15rem)_minmax(0,1fr)_minmax(0,1fr)] sm:items-center sm:px-4";

function MoveRow({ m, me }: { m: TxMove; me: number }) {
  const s = m.sides[0];
  const mine = s.team_id === me;
  return (
    <li className={`${LINE} ${mine ? "bg-foreground/[0.04]" : ""}`}>
      <Club s={s} me={mine}>
        {/* kind and time on one line on the phone; in the laptop's narrow team column they stack */}
        <p className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-xs leading-4 text-muted-foreground sm:flex-col sm:items-start">
          <span className="font-display text-[0.75rem] font-semibold uppercase tracking-[0.12em] text-foreground/75">{KIND_LABEL[m.kind]}</span>
          <time dateTime={m.at} className="tabular-nums">{stamp(m.at)}</time>
        </p>
      </Club>
      {/* each pair is one line across the two player columns; the phone stacks them under the team */}
      <div className="grid gap-y-2.5 max-sm:pl-1 sm:col-span-2 sm:grid-cols-subgrid">
        {pairs(s).map(({ added, dropped }, i) => (
          <div key={i} className="contents">
            <div className={added ? "" : "max-sm:hidden"}>{added ? <PlayerLine p={added} out={false} /> : <Empty>no add</Empty>}</div>
            <div className={dropped ? "" : "max-sm:hidden"}>{dropped ? <PlayerLine p={dropped} out /> : <Empty>no drop</Empty>}</div>
          </div>
        ))}
      </div>
    </li>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="pl-[1.875rem] font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-foreground/35">{children}</p>;
}

// A trade: two clubs facing each other across the swap mark, each over what it received (and any
// cut it made to fit it). The right side is mirrored so both halves read out from the middle.
function TradeRow({ m, me }: { m: TxMove; me: number }) {
  const [a, b] = m.sides;
  const half = (s: TxSide, mirror: boolean) => (
    <div className="min-w-0 space-y-3">
      <Club s={s} me={s.team_id === me} size={40} mirror={mirror}>
        <p className={`mt-1 ${LABEL} text-[0.75rem]`}>receives</p>
      </Club>
      <div className="space-y-2.5">
        {s.added.map((p) => <PlayerLine key={p.player_id} p={p} out={false} mirror={mirror} />)}
        {s.dropped.map((p) => <PlayerLine key={p.player_id} p={p} out mirror={mirror} />)}
      </div>
    </div>
  );
  const mine = m.sides.some((s) => s.team_id === me);
  return (
    <li className={`px-3 py-4 sm:px-4 ${mine ? "bg-foreground/[0.04]" : ""}`}>
      <p className="mb-3.5 flex items-center gap-2 text-xs text-muted-foreground">
        <Badge variant="muted" shape="chip">trade</Badge>
        <time dateTime={m.at} className="tabular-nums">{stamp(m.at)}</time>
      </p>
      {b ? (
        <div className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-start gap-x-3 sm:gap-x-6">
          {half(a, false)}
          <span className="mt-2 grid size-7 place-items-center rounded-full bg-foreground/10 sm:size-8">
            <ArrowLeftRight className="size-4" aria-label="traded with" />
          </span>
          {half(b, true)}
        </div>
      ) : (
        half(a, false)
      )}
    </li>
  );
}

export function TransactionsView({ data }: { data: Transactions }) {
  const [filter, setFilter] = useState<TxFilter>("all");
  const [mineOnly, setMineOnly] = useState(false);
  const shown = filterMoves(data.moves, filter, mineOnly ? data.me : null);
  const count = (f: TxFilter) => filterMoves(data.moves, f, mineOnly ? data.me : null).length;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
        <h1 className="font-display text-4xl font-bold leading-none tracking-tight">
          Transactions
          <span className="ml-2.5 font-semibold text-muted-foreground">{data.moves.length}</span>
        </h1>
        <StaleBadge since={data.stale_since} />
        {/* on the title line, so the filter strip keeps the full width on a phone */}
        <Toggle
          size="sm"
          variant="outline"
          pressed={mineOnly}
          onPressedChange={setMineOnly}
          className="ml-auto shrink-0 border-foreground/15 font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] aria-pressed:border-foreground/40 aria-pressed:bg-foreground aria-pressed:text-background"
        >
          My team
        </Toggle>
      </div>
      <div className="border-b border-foreground/12">
        <Tabs value={filter} onValueChange={(v) => setFilter(v as TxFilter)} aria-label="Kind of move">
          <TabsList variant="line" className="h-10 w-full justify-start gap-0 overflow-x-auto p-0 [scrollbar-width:none]">
            {TX_FILTERS.map((f) => (
              <TabsTrigger
                key={f.value}
                value={f.value}
                className="h-full flex-none gap-1.5 rounded-none px-2.5 font-display text-[0.9375rem] font-semibold uppercase tracking-[0.12em] text-foreground/55 hover:text-foreground/85 data-active:text-foreground after:inset-x-2.5 after:bottom-0 dark:text-foreground/55 dark:hover:text-foreground/85 dark:data-active:text-foreground"
              >
                {f.label}
                <span className="font-medium tracking-normal text-muted-foreground">{count(f.value)}</span>
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>
      {shown.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">
          {mineOnly ? "No moves of yours here yet." : "No moves of this kind yet."}
        </p>
      ) : (
        byWeek(shown).map(({ week, moves }) => (
          <section key={week} className="space-y-3" aria-label={weekLabel(week, data.current_week)}>
            <SectionHeading title={weekLabel(week, data.current_week)} count={moves.length} />
            <Card className={BOARD_CARD}>
              <CardContent className="px-0 sm:px-0">
                {/* the column names belong to add/drop lines; a week of trades alone goes without */}
                <div aria-hidden className={`${LINE} hidden border-b border-foreground/10 py-2 ${moves.some((m) => m.kind !== "trade") ? "sm:grid" : ""}`}>
                  <span className={`${LABEL} text-[0.75rem]`}>Team</span>
                  <span className={`${LABEL} pl-[1.875rem] text-[0.75rem]`}>Added</span>
                  <span className={`${LABEL} pl-[1.875rem] text-[0.75rem]`}>Dropped</span>
                </div>
                <ul className="divide-y divide-foreground/10">
                  {moves.map((m) => (m.kind === "trade" ? <TradeRow key={m.id} m={m} me={data.me} /> : <MoveRow key={m.id} m={m} me={data.me} />))}
                </ul>
              </CardContent>
            </Card>
          </section>
        ))
      )}
    </div>
  );
}
