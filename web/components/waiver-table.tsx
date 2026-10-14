"use client";

import {
  flexRender, getCoreRowModel, getSortedRowModel, useReactTable,
  type Column, type ColumnDef, type Row as TRow, type SortingState,
} from "@tanstack/react-table";
import { ArrowDown, ChevronDown } from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { Form, Owner, PlayerRow, RosterRow } from "@/lib/api";
import { aheadLabel, expLabel, formCell, injuryLabel, injuryTone, pts, signed } from "@/lib/format";
import { SORT_COLUMNS } from "@/lib/waiver-sort";
import { TrendArrow } from "./form-strip";
import { Kickoff } from "./local-time";
import { NAME_LINK } from "@/lib/player-log";
import { PlayerTrigger } from "./player-dialog";
import { TeamLogo } from "./player-media";
import { WaiverOutlookButton } from "./waiver-outlook";
import { ClaimControl } from "./claim-control";
import { statusTag } from "@/lib/claims";
import { SLOT_LABEL } from "@/lib/board";
import { isFreeAgent, ownerLabel, rowKind } from "@/lib/players";
import { TeamLink } from "./team-link";
import { useReorder } from "./motion";

// A worth-to-me number, or a dash where there is none (my own player).
const dash = (v: number | null) => (v == null ? "—" : signed(v));

const TAG = { ok: "secondary", warn: "warn", bad: "out" } as const;
// ESPN says ACTIVE for a player and NORMAL for a defence; neither is news.
const HEALTHY = new Set(["ACTIVE", "NORMAL"]);
// Column labels: the board face's small caps, a step smaller on a phone.
const LABEL = "font-display text-[0.6875rem] font-semibold uppercase tracking-[0.1em] text-muted-foreground sm:text-[0.8125rem] sm:tracking-[0.12em]";
// The secondary numerals: condensed, stepped back to 70% ink, a size smaller on a phone.
const SIDE = "text-right font-display text-[0.9375rem] font-medium tabular-nums tracking-tight text-foreground/70 sm:text-lg";
// Cell padding: 6px gutters on a phone, 8px from `sm`.
const PAD = "px-1.5 sm:px-2";
// The table never scrolls inside itself: it lays out to its column (the page scrolls it), so its
// one header row pins to the page under the 48px nav. A narrow column drops the three columns the
// quick read can spare (wk, ROS, beat: all in his dialog) rather than scroll sideways; the claim
// control moves onto the caption line where the claim column would not fit.
const NARROW = "@max-[32rem]/table:hidden";
const CLAIM_COL = "@max-[40rem]/table:hidden";
const CLAIM_INLINE = "@min-[40rem]/table:hidden";
// The name line's chips (would start, injury, bye) may break inside themselves in the phone's narrow
// name column: a chip is one 20px line by default, so it would otherwise run over the numbers beside it.
const WRAP_CHIPS = "[&_[data-slot=badge]]:h-auto [&_[data-slot=badge]]:min-h-5 [&_[data-slot=badge]]:max-w-full [&_[data-slot=badge]]:whitespace-normal [&_[data-slot=badge]]:text-left [&_[data-slot=badge]]:leading-4";
// The header's hairline is a shadow, not a border: a sticky cell in a collapsed table leaves its
// border behind, and the rows would show through the gap as they scroll under it.
const PIN = "sticky top-12 z-[2] bg-card shadow-[inset_0_-1px_0_color-mix(in_oklab,var(--foreground)_10%,transparent)]";

// "at LAR" when he travels, "vs SF" at home; a free agent ESPN has no venue for shows the
// opponent bare. `contents` lets the pieces wrap as items of the caption's own flex line.
function Matchup({ r }: { r: PlayerRow | RosterRow }) {
  if (!r.opponent) return <Kickoff iso={r.kickoff} bye={r.bye} />;
  return (
    <span className="contents">
      {r.home !== null && <span className="font-display font-semibold uppercase tracking-[0.12em] text-foreground/80">{r.home ? "vs" : "at"}</span>}
      <TeamLogo abbr={r.opponent} size={14} />
      <span className="font-medium text-foreground/85">{r.opponent}</span>
      <span aria-hidden>·</span>
      <Kickoff iso={r.kickoff} bye={r.bye} />
    </span>
  );
}

// The small line under a numeral: the source spread under wk, a short window's game count.
// Muted unless it is the amber spread.
function Under({ children, amber = false }: { children: React.ReactNode; amber?: boolean }) {
  return <p className={`text-[0.625rem] leading-tight tabular-nums sm:text-[0.6875rem] ${amber ? "font-medium text-close-foreground dark:text-close" : "text-muted-foreground"}`}>{children}</p>;
}

// One form number for one player. A number no game in the window has yet is an empty cell: the
// grid says it, a dash would only add ink. opp carries its trend as a drawn arrow.
function FormNumeral({ r, k }: { r: PlayerRow | RosterRow; k: keyof Form }) {
  const cell = r.form ? formCell(r.form, k) : null;
  if (!cell) return null;
  return (
    <span title={cell.title}>
      {cell.value}
      <TrendArrow trend={cell.trend} className="ml-0.5" />
      {cell.note && <Under>{cell.note}</Under>}
    </span>
  );
}

// This week's projection, its source note beneath.
function Week({ r }: { r: PlayerRow | RosterRow }) {
  return (
    <>
      <p className={`font-display text-[0.9375rem] font-semibold tabular-nums tracking-tight sm:text-lg ${r.week_proj === 0 ? "text-muted-foreground" : ""}`}>{pts(r.week_proj)}</p>
      {r.n_src === 1 ? <Under>1 source</Under> : r.spread != null && r.spread >= 3 ? <Under amber>±{pts(r.spread)}</Under> : null}
    </>
  );
}

// A column heading that sorts the table: the whole cell is the button, so the tap target is the
// header's full height; the active column carries the arrow and `aria-sort`.
function SortHead({ column, label, title }: { column: Column<PlayerRow>; label: string; title?: string }) {
  const dir = column.getIsSorted();
  const right = column.id !== "name";
  return (
    <button type="button" onClick={column.getToggleSortingHandler()} title={title}
      className={`${LABEL} flex h-9 w-full items-center gap-0.5 ${PAD} transition-colors hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-[-2px] ${right ? "justify-end" : ""} ${dir ? "text-foreground" : ""}`}>
      {label}
      {/* only the active column carries the arrow, so the others stay as narrow as their label; it fades in
          on the column tapped and turns over when the order flips */}
      {dir && <ArrowDown aria-hidden className={`fx-in size-3 stroke-[2.5] transition-transform duration-300 ease-(--ease-arrive) ${dir === "asc" ? "rotate-180" : ""}`} />}
    </button>
  );
}

// A rostered player's ESPN slot as the comparison row names it: BE reads "bench", the flex
// slot "FLEX", anything else is the slot itself.
function slotName(slot: string): string {
  return slot === "BE" ? "bench" : (SLOT_LABEL[slot] ?? slot);
}

// The positions a free agent is compared against on my roster: a flex-eligible player against
// every flex-eligible player, since any of them is the one he would replace.
const FLEX = new Set(["RB", "WR", "TE"]);
export function comparePositions(pos: string): Set<string> {
  return FLEX.has(pos) ? FLEX : new Set([pos]);
}

// The name cell every row shares: name and chips, then the caption (POS when the rows mix
// positions · TEAM · years · matchup), then the hurt teammates ahead of him. It takes the
// width the numbers leave, which on the phone is about 105px: the name, the chips and the caption all wrap,
// and a chip too wide for the column breaks inside itself (`would start` / `+14.0`). `tail` closes the
// caption line (the outlook button, on a wide table); `actions` is a line of its own under it all
// (the outlook and the claim, on a narrow one, where the caption has no room for them).
function NameCell({ r, week, showPos, mine = false, chip, tail, actions }: {
  r: PlayerRow | RosterRow; week: number; showPos: boolean; mine?: boolean; chip?: React.ReactNode; tail?: React.ReactNode; actions?: React.ReactNode;
}) {
  const hurt = !HEALTHY.has(r.injury);
  const idle = r.bye || injuryTone(r.injury) === "bad";
  return (
    <TableCell className={`w-full max-w-0 py-2 whitespace-normal ${PAD} ${mine ? "pl-4 sm:pl-5" : ""}`}>
      <p className={`flex flex-wrap items-center gap-x-1.5 gap-y-0.5 font-display text-base font-semibold leading-tight tracking-tight transition-colors duration-500 sm:text-[1.1875rem] ${idle ? "text-foreground/70" : ""} ${WRAP_CHIPS}`}>
        <PlayerTrigger p={r} className={`max-w-full break-words rounded-sm ${NAME_LINK}`}>{r.name}</PlayerTrigger>
        {chip}
        {hurt && (
          <Badge variant={TAG[injuryTone(r.injury)]} shape="chip" title={r.injury_note ?? undefined}>
            {injuryLabel(r.injury.replaceAll("_", " "), r.return_week, week)}
          </Badge>
        )}
        {r.bye && <Badge variant="muted" shape="chip">bye</Badge>}
      </p>
      <p className="mt-0.5 flex flex-wrap items-center gap-x-1 gap-y-0.5 text-[0.6875rem] leading-snug text-muted-foreground sm:text-xs">
        {showPos && <><span className="font-semibold text-foreground/80">{r.pos}</span><span aria-hidden>·</span></>}
        <span>{r.team}</span>
        {/* years in the league: R for a rookie, the one story the blend cannot tell from a hot start */}
        {expLabel(r.exp) && (
          <>
            <span aria-hidden>·</span>
            <span title={r.exp === 0 ? "rookie" : `${r.exp} years in the league`} className={r.exp === 0 ? "font-semibold text-foreground/80" : ""}>{expLabel(r.exp)}</span>
          </>
        )}
        <span aria-hidden>·</span>
        <Matchup r={r} />
        {tail}
      </p>
      {/* the hurt teammates at his position: a fill-in's window ends when they are back */}
      {aheadLabel(r.ahead, week) && (
        <p className="mt-0.5 text-[0.6875rem] leading-snug text-close-foreground sm:text-xs dark:text-close" title="teammates at his position on ESPN's injury report, with the return week the feed lists">
          <span className="font-display font-semibold uppercase tracking-[0.12em]">ahead</span>{" "}
          {aheadLabel(r.ahead, week)}
        </p>
      )}
      {actions && <div className={`mt-1 flex items-center gap-3 ${CLAIM_INLINE}`}>{actions}</div>}
    </TableCell>
  );
}

// Another team's player, where a free agent's claim control sits: the team's abbreviation,
// opening its roster. The full name rides the hover.
function OwnerChip({ owner }: { owner: Owner }) {
  return (
    <span title={owner.name ?? undefined}>
      <TeamLink id={owner.team_id} className="font-display text-[0.75rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        {ownerLabel(owner)}
      </TeamLink>
    </span>
  );
}

// ESPN's pool status beside the name: FA is added the moment the claims are submitted, so it reads
// at full ink; W·Wed (on waivers, clears Wednesday) steps back. Nothing when the read failed.
function PoolTag({ r }: { r: PlayerRow }) {
  const tag = statusTag(r);
  if (!tag) return null;
  const fa = r.status === "FREEAGENT";
  return (
    <span title={fa ? "free agent: added instantly on Submit" : "on waivers: a claim, processed when his waiver period ends"}
      className={`font-display text-[0.6875rem] font-semibold uppercase tracking-[0.12em] sm:text-[0.75rem] ${fa ? "text-foreground" : "text-muted-foreground"}`}>
      {tag}
    </span>
  );
}

// What each column is: its label, the heading's hover, the cell for a player's row, and the cell
// when one of my players stands under a free agent for the comparison (`compare`; the decision
// column shows the free agent's ROS minus his there). `className` rides both the head and the
// cells, so a column the narrow table drops goes in one place.
type Meta = {
  label: string; title?: string; className?: string;
  compare?: (fa: PlayerRow, mine: RosterRow) => React.ReactNode;
};
type Col = ColumnDef<PlayerRow> & { meta: Meta };

const FORM_COLS: { key: keyof Form & keyof typeof SORT_COLUMNS; label: string; title: string; phone: boolean }[] = [
  { key: "beat", label: "beat", phone: false, title: "points per game over his last three games minus ESPN's projection for them" },
  { key: "pace_gap", label: "pace", phone: true, title: "his last three games' pace × games left (his bye out) minus the models' rest-of-season projection: if the last three held" },
  { key: "opp", label: "opp", phone: true, title: "touches + targets per game over his last three (pass attempts for a QB; kicks for a K); the arrow is the latest game against the earlier ones" },
  { key: "snap_pct", label: "snap", phone: true, title: "share of the team's offensive snaps over his last three games (Sleeper; snaps land a day after the game)" },
];

function columns(week: number, showPos: boolean): Col[] {
  return [
    { id: "expand", enableSorting: false, meta: { label: "" } },
    {
      ...SORT_COLUMNS.name, meta: { label: "Player" },
      cell: ({ row: { original: r } }) => {
        const kind = rowKind(r);
        const free = isFreeAgent(r);
        return (
          <NameCell r={r} week={week} showPos={showPos} chip={<>
            {kind === "mine" && <Badge variant="muted" shape="chip">mine</Badge>}
            {r.would_start && <Badge variant="start" shape="chip">{free ? "start" : "would start"} {signed(r.week_delta ?? 0)}</Badge>}
            {free && <PoolTag r={r} />}
          </>} tail={free && <span className={`contents ${CLAIM_COL}`}><span aria-hidden>·</span><WaiverOutlookButton r={r} /></span>}
          actions={free
            ? <><WaiverOutlookButton r={r} /><ClaimControl id={r.player_id} name={r.name} /></>
            : kind === "theirs" && r.owner && <OwnerChip owner={r.owner} />} />
        );
      },
    },
    {
      ...SORT_COLUMNS.add,
      meta: {
        label: "add", title: "projected points gained this season by adding him",
        compare: (fa, mine) => {
          const gap = fa.ros_adj - mine.ros_adj;
          return <span title={`${fa.name}'s adjusted rest-of-season projection minus ${mine.name}'s`} className={`font-display text-base font-semibold tabular-nums tracking-tight sm:text-lg ${gap > 0 ? "" : "text-foreground/70"}`}>{signed(gap)}</span>;
        },
      },
      // `add` is the decision column and the only bold numeral; a non-positive add, or a rostered
      // player's (what he'd be worth to me, never a claim), steps back to 70% ink
      cell: ({ row: { original: r } }) => (
        <span className={`font-display text-lg font-bold tabular-nums tracking-tight sm:text-[1.5rem] ${isFreeAgent(r) && (r.add ?? 0) > 0 ? "" : "text-foreground/70"}`}>{dash(r.add)}</span>
      ),
    },
    {
      id: "claim", enableSorting: false, meta: { label: "claim", className: CLAIM_COL },
      cell: ({ row: { original: r } }) => isFreeAgent(r)
        ? <ClaimControl id={r.player_id} name={r.name} />
        : rowKind(r) === "theirs" && r.owner ? <OwnerChip owner={r.owner} /> : null,
    },
    {
      ...SORT_COLUMNS.wk, meta: { label: "wk", title: "this week's projection", className: NARROW, compare: (_, m) => <Week r={m} /> },
      cell: ({ row: { original: r } }) => <Week r={r} />,
    },
    {
      ...SORT_COLUMNS.ros,
      meta: {
        label: "ROS", className: NARROW, compare: (_, m) => pts(m.ros_adj),
        title: "usage-adjusted rest of season: the models' rate shrunk toward his last games by their snap-weighted count; what add runs on (the models' own number, their spread and the deltas are in his dialog)",
      },
      cell: ({ row: { original: r } }) => pts(r.ros_adj),
    },
    ...FORM_COLS.map((c): Col => ({
      ...SORT_COLUMNS[c.key],
      meta: { label: c.label, title: c.title, className: c.phone ? "" : NARROW, compare: (_, m) => <FormNumeral r={m} k={c.key} /> },
      cell: ({ row: { original: r } }) => <FormNumeral r={r} k={c.key} />,
    })),
  ];
}

// The cell classes of a column: the add and wk numerals are their own register, the rest stepped
// back; every number right-aligned.
function cellClass(id: string, meta: Meta): string {
  if (id === "expand" || id === "name") return "";
  const base = id === "claim" ? `${PAD}` : id === "add" || id === "wk" ? `text-right ${PAD}` : `${SIDE} ${PAD}`;
  return `${base} ${meta.className ?? ""}`;
}

// The compare chevron at the row's start: my players at his position (every flex position for a
// flex player), by ROS. My own player has nothing to compare against: the cell stays empty.
function Chevron({ row }: { row: TRow<PlayerRow> }) {
  if (!row.getCanExpand()) return null;
  const open = row.getIsExpanded();
  const what = [...comparePositions(row.original.pos)].join("/");
  return (
    <button type="button" onClick={row.getToggleExpandedHandler()} aria-expanded={open} aria-label={open ? `hide my ${what}s` : `compare with my ${what}s`}
      className="flex h-9 w-7 items-center justify-center rounded-md text-foreground/55 transition-colors hover:text-foreground focus-visible:outline-2 sm:w-8">
      <ChevronDown aria-hidden className={`size-4 stroke-[2.5] transition-transform duration-300 ease-(--ease-arrive) motion-reduce:transition-none ${open ? "rotate-180" : ""}`} />
    </button>
  );
}

// One of my players under a free agent, tinted, his slot as a muted chip; in the decision column
// the free agent's ROS minus his, so the drop reads straight down.
function CompareRow({ fa, mine, week, showPos, cols, k }: { fa: PlayerRow; mine: RosterRow; week: number; showPos: boolean; cols: Column<PlayerRow>[]; k: string }) {
  return (
    <TableRow data-key={k} className="border-foreground/10 bg-foreground/[0.03] hover:bg-foreground/[0.03]">
      {cols.map((c) => {
        const meta = c.columnDef.meta as Meta;
        if (c.id === "expand") return <TableCell key={c.id} className="w-0 p-0" />;
        if (c.id === "name") return <NameCell key={c.id} r={mine} week={week} showPos={showPos} mine chip={<Badge variant="muted" shape="chip">{slotName(mine.slot)}</Badge>} />;
        return <TableCell key={c.id} className={cellClass(c.id, meta)}>{meta.compare?.(fa, mine)}</TableCell>;
      })}
    </TableRow>
  );
}

// The player pool at one position: shadcn's Table driven by TanStack (sorting, the compare
// rows). Rows arrive in the API's `add` order and open that way; a heading re-sorts them (numbers
// high first, the name A to Z; a second tap flips it). The column set is the quick read: add and
// the claim, this week and the rest of the season, then how his last three games ran against the
// projection (beat, pace) and his usage (opp, snap). Everything else (the models' ROS and spread,
// the two deltas, L3, tgt/snap, ypt, wt) is in his dialog.
export function WaiverTable({ rows, roster, week, pos, q, fa = true }: { rows: PlayerRow[]; roster: RosterRow[]; week: number; pos: string; q?: string | null; fa?: boolean }) {
  const [sorting, setSorting] = useState<SortingState>([{ id: "add", desc: true }]);
  // a search spans every position, so the caption names his; a position tab already does
  const showPos = !!q;
  // only the form columns this position has: a kicker never has snaps, a defence no
  // opportunity, and before the week's snaps land nobody has a snap share yet
  const keep = useMemo(() => new Set(FORM_COLS.filter((c) => rows.some((r) => r.form && formCell(r.form, c.key))).map((c) => c.key as string)), [rows]);
  const cols = useMemo(() => columns(week, showPos).filter((c) => !FORM_COLS.some((f) => f.key === c.id) || keep.has(c.id!)), [week, showPos, keep]);
  // eslint-disable-next-line react-hooks/incompatible-library -- TanStack v8 hands back a mutable instance; the compiler leaves this component unmemoized
  const table = useReactTable({
    data: rows, columns: cols, state: { sorting }, onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(),
    getRowId: (r) => String(r.player_id), getRowCanExpand: (r) => rowKind(r.original) !== "mine",
    enableSortingRemoval: false,
  });
  // a re-sort slides every row to its new place; opening a comparison slides the rows below down
  // to make room while my players fade in under him (motion.tsx)
  const order = table.getRowModel().rows.map((r) => `${r.id}${r.getIsExpanded() ? "+" : ""}`).join();
  const body = useReorder<HTMLTableSectionElement>(order);
  if (rows.length === 0) {
    // every rostered player is in the pool, the free agents only to ESPN's top 150 at each position (espn_season.POOL_SIZE)
    return (
      <p className="max-w-[60ch] py-6 text-sm text-muted-foreground">
        {q
          ? <>No player matching <span className="font-medium text-foreground">&ldquo;{q}&rdquo;</span>, rostered or among ESPN&apos;s top 150 free agents at each position.</>
          : fa ? <>ESPN lists no free agents at {pos} right now.</> : <>Nobody at {pos} right now.</>}
      </p>
    );
  }
  const leaf = table.getVisibleLeafColumns();
  // the card lets the header out to pin to the page (no overflow clip); the header's end cells
  // round their own top corners to the card's
  return (
    <div className="@container/table">
      <Card className="gap-0 overflow-visible rounded-lg py-0 shadow-[0_10px_28px_-16px_rgb(0_0_0_/_0.55)]">
        <Table containerClassName="overflow-visible" className="[&_th:first-child]:rounded-tl-lg [&_th:last-child]:rounded-tr-lg">
          <TableHeader className="[&_tr]:border-0">
            {table.getHeaderGroups().map((g) => (
              <TableRow key={g.id} className="hover:bg-transparent">
                {g.headers.map((h) => {
                  const meta = h.column.columnDef.meta as Meta;
                  const sorted = h.column.getIsSorted();
                  return (
                    <TableHead key={h.id} aria-sort={sorted ? (sorted === "asc" ? "ascending" : "descending") : undefined}
                      className={`h-9 p-0 ${PIN} ${h.column.id === "expand" ? "w-0" : ""} ${meta.className ?? ""}`}>
                      {h.column.getCanSort()
                        ? <SortHead column={h.column} label={meta.label} title={meta.title} />
                        : meta.label && <span className={`${LABEL} block ${PAD}`}>{meta.label}</span>}
                    </TableHead>
                  );
                })}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody ref={body}>
            {table.getRowModel().rows.map((row) => {
              const open = row.getIsExpanded();
              const mine = open ? roster.filter((m) => comparePositions(row.original.pos).has(m.pos)) : [];
              return [
                <TableRow key={row.id} data-key={row.id} className={`border-foreground/10 hover:bg-transparent ${open ? "border-b-0" : ""}`}>
                  {row.getVisibleCells().map((cell) => {
                    const meta = cell.column.columnDef.meta as Meta;
                    if (cell.column.id === "expand") return <TableCell key={cell.id} className="w-0 py-2 pr-0 pl-0.5 sm:pl-1.5"><Chevron row={row} /></TableCell>;
                    // the name column's cell is a whole <td> (NameCell), shared with the compare rows
                    if (cell.column.id === "name") return <Fragment key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</Fragment>;
                    return <TableCell key={cell.id} className={cellClass(cell.column.id, meta)}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</TableCell>;
                  })}
                </TableRow>,
                ...mine.map((m) => <CompareRow key={`${row.id}-${m.player_id}`} k={`${row.id}-${m.player_id}`} fa={row.original} mine={m} week={week} showPos={showPos} cols={leaf} />),
                open && mine.length === 0 && (
                  <TableRow key={`${row.id}-none`} data-key={`${row.id}-none`} className="border-foreground/10 bg-foreground/[0.03] hover:bg-foreground/[0.03]">
                    <TableCell colSpan={leaf.length} className="py-2 pl-4 text-xs text-muted-foreground sm:pl-5">nobody at {row.original.pos} on my roster</TableCell>
                  </TableRow>
                ),
              ];
            })}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}

