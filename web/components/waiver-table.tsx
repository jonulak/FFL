"use client";

import { Empty, EmptyDescription, EmptyHeader } from "@/components/ui/empty";
import {
  flexRender, getCoreRowModel, getSortedRowModel, useReactTable,
  type Column, type ColumnDef, type Row as TRow, type SortingState, type Updater,
} from "@tanstack/react-table";
import { ArrowDown, ChevronDown } from "lucide-react";
import { Fragment, memo, startTransition, useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { Form, Owner, PlayerRow, RosterRow } from "@/lib/api";
import { aheadLabel, expLabel, formCell, injuryLabel, injuryTone, pts, signed } from "@/lib/format";
import { SORT_COLUMNS, columnSpec, sortFor, type ColumnId } from "@/lib/player-columns";
import { ALL, FLEX as FLEX_TAB, isGroup } from "@/lib/player-view";
import { TrendArrow } from "./form-strip";
import { Kickoff } from "./local-time";
import { NAME_LINK } from "@/lib/player-log";
import { PlayerTrigger } from "./player-dialog";
import { TeamLogo } from "./player-media";
import { WaiverOutlookButton } from "./waiver-outlook";
import { ClaimControl } from "./claim-control";
import { WatchStar } from "./watch-star";
import { statusTag } from "@/lib/claims";
import { SLOT_LABEL } from "@/lib/board";
import { isFreeAgent, ownerLabel, rowKind } from "@/lib/players";
import { TeamLink } from "./team-link";
import { useOpenLeague } from "./league-context";
import { useReorder } from "./motion";
import { Hint } from "./hint";
import { usePlayerView } from "./player-view-context";
import { useSyncedHeader } from "./use-synced-header";

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
// The view picks the columns, so the numbers scroll sideways under a pinned name column when they
// outgrow the card; the claim control moves onto the caption line where the claim column would not fit.
const CLAIM_COL = "@max-[40rem]/table:hidden";
const CLAIM_INLINE = "@min-[40rem]/table:hidden";
// The name line's chips (would start, injury, bye) may break inside themselves in the phone's narrow
// name column: a chip is one 20px line by default, so it would otherwise run over the numbers beside it.
const WRAP_CHIPS = "[&_[data-slot=badge]]:h-auto [&_[data-slot=badge]]:min-h-5 [&_[data-slot=badge]]:max-w-full [&_[data-slot=badge]]:whitespace-normal [&_[data-slot=badge]]:text-left [&_[data-slot=badge]]:leading-4";
// The heading box pins under the nav (--nav-h: two rows on the phone, 48px from sm). Its hairline is an inset shadow, not a border, so it pins
// with the box; the pinned chevron and name headings lay their own ground over it and redraw it.
const PIN = "sticky top-(--nav-h) z-[2] bg-card shadow-[inset_0_-1px_0_color-mix(in_oklab,var(--foreground)_10%,transparent)]";
// The chevron and the name stay put while the numbers scroll: each pinned to the left on an opaque
// ground, the name offset by the chevron's exact width. Once the body has scrolled (use-synced-header
// marks both boxes), the name's edge casts a thin shadow over the numbers passing under it.
const EXPAND_PIN = "sticky left-0 z-[1] w-7 min-w-7 max-w-7 p-0 sm:w-8 sm:min-w-8 sm:max-w-8";
const NAME_PIN = "sticky left-7 z-[1] w-[10.5rem] min-w-[10.5rem] max-w-[10.5rem] sm:left-8 sm:w-[14rem] sm:min-w-[14rem] sm:max-w-[14rem]";
const EDGE = "[[data-scrolled]_&]:shadow-[6px_0_6px_-6px_rgb(0_0_0_/_0.35)]";
const HEAD_EXPAND = "shadow-[inset_0_-1px_0_color-mix(in_oklab,var(--foreground)_10%,transparent)]";
const HEAD_EDGE = `${HEAD_EXPAND} [[data-scrolled]_&]:shadow-[inset_0_-1px_0_color-mix(in_oklab,var(--foreground)_10%,transparent),6px_0_6px_-6px_rgb(0_0_0_/_0.35)]`;
// The pinned cells' ground: the card (or, on a row whose comparison is open, the row's muted wash laid
// over it, as TableRow draws it), or the comparison rows' 3% tint laid over it.
const GROUND = "bg-card in-[tr:has([aria-expanded=true])]:bg-[color-mix(in_oklab,var(--muted)_50%,var(--card))]";
const TINT = "bg-[color-mix(in_oklab,var(--foreground)_3%,var(--card))]";

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
    <Hint text={title} side="bottom">
      <button type="button" onClick={column.getToggleSortingHandler()}
        className={`${LABEL} flex h-9 w-full items-center gap-0.5 ${PAD} transition-colors hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-[-2px] ${right ? "justify-end" : ""} ${dir ? "text-foreground" : ""}`}>
        {label}
        {/* only the active column carries the arrow, so the others stay as narrow as their label; it fades in
            on the column tapped and turns over when the order flips */}
        {dir && <ArrowDown aria-hidden className={`fx-in size-3 stroke-[2.5] transition-transform duration-300 ease-(--ease-arrive) ${dir === "asc" ? "rotate-180" : ""}`} />}
      </button>
    </Hint>
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
// positions · TEAM · years · matchup), then the hurt teammates ahead of him. It is pinned at a
// fixed width (168px on the phone, room for the outlook and the claim side by side; 224px from `sm`):
// the name, the chips and the caption all wrap, and a chip too wide for the column breaks inside
// itself (`would start` / `+14.0`). `tail` closes the caption line (the outlook button and the watch star, on a wide
// table); `actions` is a line of its own under it all (the star, the outlook and the claim, on a narrow one,
// where the caption has no room for them).
function NameCell({ r, week, showPos, mine = false, chip, tail, actions }: {
  r: PlayerRow | RosterRow; week: number; showPos: boolean; mine?: boolean; chip?: React.ReactNode; tail?: React.ReactNode; actions?: React.ReactNode;
}) {
  const hurt = !HEALTHY.has(r.injury);
  const idle = r.bye || injuryTone(r.injury) === "bad";
  return (
    <TableCell className={`${NAME_PIN} ${EDGE} ${mine ? TINT : GROUND} py-2 whitespace-normal ${PAD} ${mine ? "pl-4 sm:pl-5" : ""}`}>
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
      {actions && <div className={`mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 ${CLAIM_INLINE}`}>{actions}</div>}
    </TableCell>
  );
}

// Another team's player, where a free agent's claim control sits: the team's abbreviation,
// opening its roster. The full name rides the hover.
// A free agent's outlook (and the dot before it): home's alone, the API refuses another league's (4.7).
function OutlookDot({ r, dot = false }: { r: Parameters<typeof WaiverOutlookButton>[0]["r"]; dot?: boolean }) {
  const { home } = useOpenLeague();
  if (!home) return null;
  return <>{dot && <span aria-hidden>·</span>}<WaiverOutlookButton r={r} /></>;
}

function OwnerChip({ owner }: { owner: Owner }) {
  const { home } = useOpenLeague();
  return (
    <span title={owner.name ?? undefined}>
      <TeamLink linked={home} id={owner.team_id} className="font-display text-[0.75rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
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
type Cells = { cell: (r: PlayerRow) => React.ReactNode; compare?: (fa: PlayerRow, mine: RosterRow) => React.ReactNode };

const form = (k: keyof Form): Cells => ({ cell: (r) => <FormNumeral r={r} k={k} />, compare: (_, m) => <FormNumeral r={m} k={k} /> });
const spread = (r: PlayerRow | RosterRow) => (r.ros_spread == null ? "" : `±${pts(r.ros_spread)}`);

// How each column a view can pick draws its cells (its heading and its sort are lib/player-columns').
const CELLS: Record<ColumnId, Cells> = {
  // `add` is the decision column and the only bold numeral; a non-positive add, or a rostered
  // player's (what he'd be worth to me, never a claim), steps back to 70% ink
  add: {
    cell: (r) => <span className={`font-display text-lg font-bold tabular-nums tracking-tight sm:text-[1.5rem] ${isFreeAgent(r) && (r.add ?? 0) > 0 ? "" : "text-foreground/70"}`}>{dash(r.add)}</span>,
    compare: (fa, mine) => {
      const gap = fa.ros_adj - mine.ros_adj;
      return <span title={`${fa.name}'s adjusted rest-of-season projection minus ${mine.name}'s`} className={`font-display text-base font-semibold tabular-nums tracking-tight sm:text-lg ${gap > 0 ? "" : "text-foreground/70"}`}>{signed(gap)}</span>;
    },
  },
  wk: { cell: (r) => <Week r={r} />, compare: (_, m) => <Week r={m} /> },
  ros: { cell: (r) => pts(r.ros_adj), compare: (_, m) => pts(m.ros_adj) },
  ros_model: { cell: (r) => pts(r.ros_proj), compare: (_, m) => pts(m.ros_proj) },
  ros_spread: { cell: spread, compare: (_, m) => spread(m) },
  // against my roster and the league's replacement: one of my own players has neither
  d_roster: { cell: (r) => dash(r.delta_roster) },
  d_pool: { cell: (r) => dash(r.delta_pool) },
  exp: { cell: (r) => expLabel(r.exp), compare: (_, m) => expLabel(m.exp) },
  l3: form("l3"), beat: form("beat"), pace_gap: form("pace_gap"), opp: form("opp"), snap_pct: form("snap_pct"),
  tgt_snap: form("tgt_snap"), ypt: form("ypt"), weight: form("weight"), air_share: form("air_share"), air_catch: form("air_catch"),
  rz_tgt: form("rz_tgt"), rz_att: form("rz_att"), rz_pass: form("rz_pass"),
};

// The chevron, the name, then the view's columns in its order; the claim stands beside add when the
// view shows add, else straight after the name.
function columns(week: number, showPos: boolean, cols: string[]): Col[] {
  const name: Col = {
    ...SORT_COLUMNS.name, meta: { label: "Player" },
    cell: ({ row: { original: r } }) => {
      const kind = rowKind(r);
      const free = isFreeAgent(r);
      return (
        <NameCell r={r} week={week} showPos={showPos} chip={<>
          {kind === "mine" && <Badge variant="muted" shape="chip">mine</Badge>}
          {r.would_start && <Badge variant="start" shape="chip">{free ? "start" : "would start"} {signed(r.week_delta ?? 0)}</Badge>}
          {free && <PoolTag r={r} />}
        </>} tail={kind !== "mine" && <span className={`contents ${CLAIM_COL}`}>
          {free && <OutlookDot r={r} dot />}
          <WatchStar id={r.player_id} name={r.name} className="ml-0.5" />
        </span>}
        actions={kind !== "mine" && <>
          <WatchStar id={r.player_id} name={r.name} />
          {free
            ? <><OutlookDot r={r} /><ClaimControl id={r.player_id} name={r.name} /></>
            : r.owner && <OwnerChip owner={r.owner} />}
        </>} />
      );
    },
  };
  const claim: Col = {
    id: "claim", enableSorting: false, meta: { label: "claim", className: CLAIM_COL },
    cell: ({ row: { original: r } }) => isFreeAgent(r)
      ? <ClaimControl id={r.player_id} name={r.name} />
      : rowKind(r) === "theirs" && r.owner ? <OwnerChip owner={r.owner} /> : null,
  };
  const picked = cols.flatMap((id): Col[] => {
    const spec = columnSpec(id);
    if (!spec) return [];
    const c = CELLS[spec.id];
    return [{
      ...SORT_COLUMNS[spec.id], meta: { label: spec.label, title: spec.title, compare: c.compare },
      cell: ({ row: { original: r } }) => c.cell(r),
    }];
  });
  const at = picked.findIndex((c) => c.id === "add") + 1;
  return [{ id: "expand", enableSorting: false, meta: { label: "" } }, name, ...picked.slice(0, at), claim, ...picked.slice(at)];
}

// The cell classes of a column: the add and wk numerals are their own register, the rest stepped
// back; every number right-aligned, on one line.
function cellClass(id: string, meta: Meta): string {
  if (id === "expand" || id === "name") return "";
  const base = id === "claim" ? `${PAD}` : id === "add" || id === "wk" ? `text-right ${PAD}` : `${SIDE} ${PAD}`;
  return `${base} whitespace-nowrap ${meta.className ?? ""}`;
}

// The compare chevron at the row's start: my players at his position (every flex position for a
// flex player), by ROS. My own player has nothing to compare against: the cell stays empty.
function Chevron({ row }: { row: TRow<PlayerRow> }) {
  if (!row.getCanExpand()) return null;
  const open = row.getIsExpanded();
  const what = [...comparePositions(row.original.pos)].join("/");
  return (
    <Button type="button" variant="ghost" size="icon-sm" onClick={row.getToggleExpandedHandler()} aria-expanded={open} aria-label={open ? `hide my ${what}s` : `compare with my ${what}s`}
      className="h-9 w-full rounded-md text-foreground/55 hover:bg-transparent dark:hover:bg-transparent hover:text-foreground aria-expanded:bg-transparent aria-expanded:text-foreground/55 sm:w-8">
      <ChevronDown aria-hidden className={`size-4 stroke-[2.5] transition-transform duration-300 ease-(--ease-arrive) motion-reduce:transition-none ${open ? "rotate-180" : ""}`} />
    </Button>
  );
}

// One of my players under a free agent, tinted, his slot as a muted chip; in the decision column
// the free agent's ROS minus his, so the drop reads straight down.
function CompareRow({ fa, mine, week, showPos, cols, k }: { fa: PlayerRow; mine: RosterRow; week: number; showPos: boolean; cols: Column<PlayerRow>[]; k: string }) {
  return (
    <TableRow data-key={k} className="border-foreground/10 bg-foreground/[0.03] hover:bg-foreground/[0.03]">
      {cols.map((c) => {
        const meta = c.columnDef.meta as Meta;
        if (c.id === "expand") return <TableCell key={c.id} className={`${EXPAND_PIN} ${TINT}`} />;
        if (c.id === "name") return <NameCell key={c.id} r={mine} week={week} showPos={showPos} mine chip={<Badge variant="muted" shape="chip">{slotName(mine.slot)}</Badge>} />;
        return <TableCell key={c.id} className={cellClass(c.id, meta)}>{meta.compare?.(fa, mine)}</TableCell>;
      })}
    </TableRow>
  );
}

// One player's row and, while it is open, my players under him. Memoized on his data, not on
// TanStack's row: a sort hands back fresh row objects for the same players, and re-rendering all
// 150 rows (each with its menus, dialog and chips) was the whole cost of a tap on a heading. A
// skipped row keeps its old `row`, whose handlers still reach the table by id.
type PoolRowProps = { row: TRow<PlayerRow>; open: boolean; cols: Col[]; roster: RosterRow[]; week: number; showPos: boolean };
const PoolRow = memo(function PoolRow({ row, open, roster, week, showPos }: PoolRowProps) {
  const mine = open ? roster.filter((m) => comparePositions(row.original.pos).has(m.pos)) : [];
  const cells = row.getVisibleCells();
  const leaf = cells.map((c) => c.column);
  return (
    <>
      <TableRow data-key={row.id} className={`border-foreground/10 hover:bg-transparent ${open ? "border-b-0" : ""}`}>
        {cells.map((cell) => {
          const meta = cell.column.columnDef.meta as Meta;
          if (cell.column.id === "expand") return <TableCell key={cell.id} className={`${EXPAND_PIN} ${GROUND} py-2`}><Chevron row={row} /></TableCell>;
          // the name column's cell is a whole <td> (NameCell), shared with the compare rows
          if (cell.column.id === "name") return <Fragment key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</Fragment>;
          return <TableCell key={cell.id} className={cellClass(cell.column.id, meta)}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</TableCell>;
        })}
      </TableRow>
      {mine.map((m) => <CompareRow key={`${row.id}-${m.player_id}`} k={`${row.id}-${m.player_id}`} fa={row.original} mine={m} week={week} showPos={showPos} cols={leaf} />)}
      {open && mine.length === 0 && (
        <TableRow data-key={`${row.id}-none`} className="border-foreground/10 bg-foreground/[0.03] hover:bg-foreground/[0.03]">
          <TableCell colSpan={leaf.length} className="py-2 pl-4 text-xs text-muted-foreground sm:pl-5"><span className="sticky left-4 sm:left-5">nobody at {row.original.pos} on my roster</span></TableCell>
        </TableRow>
      )}
    </>
  );
}, (a, b) => a.row.original === b.row.original && a.open === b.open && a.cols === b.cols && a.roster === b.roster && a.week === b.week && a.showPos === b.showPos);

// The first rows, drawn by the server and hydrated with the page; the rest land after the first paint.
// A filtered FLEX or ALL view can run to 400 rows (26,000 nodes), and drawing them all at once held the
// phone for most of a second on load and on every tab tap.
const FIRST = 40;
const STEP = 60;

// A zero-height, invisible first row carrying each heading's label (and room for the sort arrow), so
// the body's columns are never narrower than their headings: the heading is another table, sized to
// the body (use-synced-header), and a label wider than its numbers would otherwise be clipped.
function Sizer({ cols }: { cols: Column<PlayerRow>[] }) {
  return (
    <tr aria-hidden className="invisible border-0">
      {cols.map((c) => {
        const meta = c.columnDef.meta as Meta;
        if (c.id === "expand") return <td key={c.id} className={EXPAND_PIN} />;
        if (c.id === "name") return <td key={c.id} className={`${NAME_PIN} p-0`} />;
        return (
          <td key={c.id} className={`h-0 p-0 ${meta.className ?? ""}`}>
            <span className={`${LABEL} flex h-0 gap-0.5 overflow-hidden whitespace-nowrap ${PAD}`}>{meta.label}{c.getCanSort() && <span className="w-3 shrink-0" />}</span>
          </td>
        );
      })}
    </tr>
  );
}

// The player pool the view picks: shadcn's Table driven by TanStack (sorting, the compare rows).
// The columns are the view's; rows arrive in its sort, and a heading re-sorts them here (numbers high
// first, the name A to Z; a second tap flips it), writing the new sort into the URL without a fetch.
// The numbers scroll sideways under the pinned chevron and name; the heading is its own table,
// pinned under the nav and kept to the body's sideways scroll.
export function WaiverTable({ rows, roster, week, pos, q, fa = true }: { rows: PlayerRow[]; roster: RosterRow[]; week: number; pos: string; q?: string | null; fa?: boolean }) {
  const { view, setSortLocal } = usePlayerView();
  // a search spans every position, and ALL and FLEX several, so the caption names his; a position tab already does
  const showPos = !!q || isGroup(pos);
  const colKey = view.cols.join();
  // keyed on the column ids, not the array, so PoolRow's memo holds across a heading tap
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const cols = useMemo(() => columns(week, showPos, view.cols), [week, showPos, colKey]);
  const sort = sortFor(view.cols, view.sort);
  // one array per sort, not per render: TanStack memoizes the sorted rows on its identity, and a fresh
  // one each render re-sorts, which queues its page-index reset (a state change), which renders again,
  // without end
  const sorting: SortingState = useMemo(() => [{ id: sort.id, desc: sort.desc }], [sort.id, sort.desc]);
  const onSortingChange = (u: Updater<SortingState>) => {
    const next = typeof u === "function" ? u(sorting) : u;
    if (next[0]) setSortLocal({ id: next[0].id, desc: next[0].desc });
  };
  // eslint-disable-next-line react-hooks/incompatible-library -- TanStack v8 hands back a mutable instance; the compiler leaves this component unmemoized
  const table = useReactTable({
    data: rows, columns: cols, state: { sorting }, onSortingChange,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(),
    getRowId: (r) => String(r.player_id), getRowCanExpand: (r) => rowKind(r.original) !== "mine",
    enableSortingRemoval: false,
  });
  // a re-sort slides every row to its new place; opening a comparison slides the rows below down
  // to make room while my players fade in under him (motion.tsx)
  const order = table.getRowModel().rows.map((r) => `${r.id}${r.getIsExpanded() ? "+" : ""}`).join();
  const bodyReorder = useReorder<HTMLTableSectionElement>(order);
  const { head, body } = useSyncedHeader<HTMLDivElement, HTMLDivElement>(`${colKey}|${rows.length}|${rows[0]?.player_id}`);
  // the same FIRST on the server and in the browser, so hydration walks only those; the slice is taken
  // at render, over the whole sorted model, so a heading tap before the rest land still shows the top
  // ones, and `order` (every row) does not change when they land, so nothing slides in. A new set of
  // rows (a tab, a filter) starts again from FIRST; the rest land a STEP at a time, one task each.
  const [shown, setShown] = useState(FIRST);
  const [seenRows, setSeenRows] = useState(rows);
  if (seenRows !== rows) {
    setSeenRows(rows);
    setShown(FIRST);
  }
  useEffect(() => {
    if (shown >= rows.length) return;
    const id = setTimeout(() => startTransition(() => setShown((n) => n + STEP)), 0);
    return () => clearTimeout(id);
  }, [shown, rows.length]);
  if (rows.length === 0) {
    const where = pos === ALL ? "every position" : pos === FLEX_TAB ? "RB, WR or TE" : pos;
    // every rostered player is in the pool, the free agents only to ESPN's top 150 at each position (espn_season.POOL_SIZE)
    return (
      <Empty className="py-6">
        <EmptyHeader className="max-w-[60ch]">
          <EmptyDescription>
            {q
              ? <>No player matching <span className="font-medium text-foreground">&ldquo;{q}&rdquo;</span>, rostered or among ESPN&apos;s top 150 free agents at each position.</>
              : view.filters.length > 0 ? <>No player passes these filters at {where}.</>
              : fa ? <>ESPN lists no free agents at {where} right now.</> : <>Nobody at {where} right now.</>}
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    );
  }
  const leaf = table.getVisibleLeafColumns();
  // the card lets the heading out to pin to the page (no overflow clip); the heading box and the body
  // box round their own corners to the card's
  return (
    <div className="@container/table">
      <Card className="gap-0 overflow-visible rounded-lg py-0 shadow-[0_10px_28px_-16px_rgb(0_0_0_/_0.55)]">
        {/* the heading: its own table, pinned under the nav, clipped and kept to the body's sideways scroll */}
        <div ref={head} className={`overflow-hidden rounded-t-lg ${PIN}`}>
          <Table containerClassName="overflow-visible" className="w-max min-w-full table-fixed">
            <TableHeader className="[&_tr]:border-0">
              {table.getHeaderGroups().map((g) => (
                <TableRow key={g.id} className="hover:bg-transparent">
                  {g.headers.map((h) => {
                    const meta = h.column.columnDef.meta as Meta;
                    const sorted = h.column.getIsSorted();
                    const pin = h.column.id === "expand" ? `${EXPAND_PIN} ${HEAD_EXPAND} ${GROUND}` : h.column.id === "name" ? `${NAME_PIN} ${HEAD_EDGE} ${GROUND}` : "";
                    return (
                      <TableHead key={h.id} aria-sort={sorted ? (sorted === "asc" ? "ascending" : "descending") : undefined}
                        className={`h-9 p-0 ${pin} ${meta.className ?? ""}`}>
                        {h.column.getCanSort()
                          ? <SortHead column={h.column} label={meta.label} title={meta.title} />
                          : meta.label && <span className={`${LABEL} block ${PAD}`}>{meta.label}</span>}
                      </TableHead>
                    );
                  })}
                </TableRow>
              ))}
            </TableHeader>
          </Table>
        </div>
        {/* no rubber-band at either end: the heading follows scrollLeft, which never goes past the edges, so a bounce slid the numbers out from under it */}
        <div ref={body} className="overflow-x-auto overscroll-x-none rounded-b-lg [scrollbar-width:thin]">
          <Table containerClassName="overflow-visible" className="w-max min-w-full">
            <TableBody ref={bodyReorder}>
              <Sizer cols={leaf} />
              {table.getRowModel().rows.slice(0, shown).map((row) => (
                <PoolRow key={row.id} row={row} open={row.getIsExpanded()} cols={cols} roster={roster} week={week} showPos={showPos} />
              ))}
            </TableBody>
          </Table>
        </div>
      </Card>
    </div>
  );
}
