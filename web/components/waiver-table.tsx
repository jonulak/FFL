"use client";

import { ArrowDown, ArrowUp } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { WaiverRow } from "@/lib/api";
import { FORM_COLUMNS, formCell, injuryLabel, injuryTone, pts, signed } from "@/lib/format";
import { sortRows, type Sort } from "@/lib/waiver-sort";
import { TrendArrow } from "./form-strip";
import { Kickoff } from "./local-time";
import { TeamLogo, TeamPanel } from "./player-media";

const TAG = { ok: "secondary", warn: "warn", bad: "out" } as const;
// ESPN says ACTIVE for a player and NORMAL for a defence; neither is news.
const HEALTHY = new Set(["ACTIVE", "NORMAL"]);
const LABEL = "font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground";
// The stat-block headings over the two groups of secondary columns: the label style a step
// smaller and lighter, with the hairline under it running the width of its group only.
const GROUP = "border-b border-foreground/12 pb-0 pl-4 pr-2 align-bottom font-display text-[0.6875rem] font-semibold uppercase tracking-[0.14em] text-foreground/55";
// The secondary numerals (ROS, the two deltas, the seven form columns): condensed, stepped back,
// the way the League table sets points against. Right-aligned, 8px gutters. The first column
// of each group is set off from the last of the one before by a 16px gutter and a hairline.
const SIDE = "text-right font-display text-lg font-medium tabular-nums tracking-tight text-foreground/70";
const GROUP_START = "border-l border-foreground/10 pl-4";
// The table scrolls inside a panel no taller than the viewport under the nav, both ways, so the
// two header rows can pin to its top and the tile and name to its left while the numerals
// scroll; from `xl` the row fits the page and the panel is the page again, the headers pinned
// under the 48px nav instead. Pinned cells carry the card's own background. Their layers stay
// under the nav's z-10, or a pinned name would paint over the nav as the page scrolls under it:
// body pins at 1, headers at 2, the corner cells that pin both ways at 3.
const PANEL = "max-h-[calc(100dvh-3rem)] overflow-auto overscroll-x-contain xl:max-h-none xl:overflow-visible";
const PIN_TOP_1 = "sticky top-0 z-[2] bg-card xl:top-12";
const PIN_TOP_2 = "sticky top-7 z-[2] bg-card xl:top-[4.75rem]";
const PIN_CORNER = "z-[3]";
const PIN_TILE = "sticky left-0 z-[1] bg-card";
const PIN_NAME = "sticky left-12 z-[1] bg-card sm:left-14 max-xl:border-r max-xl:border-foreground/10";
// The two ROS sources disagreeing by this much over the rest of the season is worth a look — the
// weekly column's 3-point rule (CLOSE_SPREAD), scaled to the weeks left, rounded to a whole number.
const ROS_CLOSE = 20;

// A WaiverRow's ros_proj is always a number (the API fills the season formula in when no source
// has him), so "sourced" is read off the two source columns.
function sourced(r: WaiverRow): boolean {
  return r.ros_sleeper != null || r.ros_cbs != null;
}

// Where ROS came from, for the hover: both numbers when both sources have him, the one when one
// does, and the season formula when neither.
function rosSources(r: WaiverRow): string {
  if (!sourced(r)) return "no rest-of-season source has him: season projection scaled by the weeks left";
  const parts = [];
  if (r.ros_sleeper != null) parts.push(`Sleeper (Rotowire) weeks left ${pts(r.ros_sleeper)}`);
  if (r.ros_cbs != null) parts.push(`CBS rest of season ${pts(r.ros_cbs)}`);
  return parts.join(" · ");
}

// "at LAR" when he travels, "vs SF" at home; a free agent ESPN has no venue for shows the
// opponent bare. `contents` lets the pieces wrap as items of the caption's own flex line.
function Matchup({ r }: { r: WaiverRow }) {
  if (!r.opponent) return <Kickoff iso={r.kickoff} bye={r.bye} />;
  return (
    <span className="contents">
      {r.home !== null && <span className="font-display text-xs font-semibold uppercase tracking-[0.12em] text-foreground/80">{r.home ? "vs" : "at"}</span>}
      <TeamLogo abbr={r.opponent} size={16} />
      <span className="font-medium text-foreground/85">{r.opponent}</span>
      <span aria-hidden>·</span>
      <Kickoff iso={r.kickoff} bye={r.bye} />
    </span>
  );
}

// The small line under a secondary numeral: the source spread under ROS, a short window's game
// count under L3. Muted unless it is the amber spread.
function Under({ children, amber = false }: { children: React.ReactNode; amber?: boolean }) {
  return <p className={`text-[0.6875rem] leading-tight tabular-nums ${amber ? "font-medium text-close-foreground dark:text-close" : "text-muted-foreground"}`}>{children}</p>;
}

type FormCol = (typeof FORM_COLUMNS)[number];

// One of the seven form columns for one player. A column that no game in the window has yet is
// an empty cell: the grid says it, a dash would only add ink. The opportunity cell carries the
// trend as a drawn arrow after the number.
function FormNumeral({ r, col }: { r: WaiverRow; col: FormCol }) {
  const cell = r.form ? formCell(r.form, col.key) : null;
  if (!cell) return null;
  return (
    <span title={cell.title}>
      {cell.value}
      <TrendArrow trend={cell.trend} className="ml-0.5" />
      {cell.note && <Under>{cell.note}</Under>}
    </span>
  );
}

// A column heading that sorts the table: the whole cell is the button, so the tap target is the
// header's full height; the active column carries the arrow and `aria-sort`.
function SortHead({ id, sort, onSort, className = "", title, children }: {
  id: string; sort: Sort; onSort: (key: string) => void; className?: string; title?: string; children: React.ReactNode;
}) {
  const active = sort.key === id;
  const Arrow = sort.dir === "asc" ? ArrowUp : ArrowDown;
  return (
    <TableHead aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : undefined} className={`h-9 p-0 ${className}`} title={title}>
      <button type="button" onClick={() => onSort(id)}
        className={`${LABEL} flex h-9 w-full items-center gap-0.5 px-2 hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-[-2px] ${className.includes("text-right") ? "justify-end" : ""} ${active ? "text-foreground" : ""}`}>
        {children}
        {/* only the active column carries the arrow, so the other columns stay as narrow as their label */}
        {active && <Arrow aria-hidden className="size-3 stroke-[2.5]" />}
      </button>
    </TableHead>
  );
}

// One free agent, one row: the Matchup page's lineup row with the waiver numbers beside it.
// `add` is the decision column and the only bold numeral; a non-positive add steps back to 70%
// ink so the one worth a claim, when there is one, reads first. A player who would go straight
// into this week's optimal lineup carries the START chip with the lineup gain — the same
// meaning lime has everywhere else on the board. Two stat blocks follow: the rest of season
// (ROS and the two deltas) and his last three games; on a phone they scroll sideways under the
// pinned tile and name rather than fold away.
function Row({ r, week, cols }: { r: WaiverRow; week: number; cols: FormCol[] }) {
  const hurt = !HEALTHY.has(r.injury);
  const idle = r.bye || injuryTone(r.injury) === "bad";
  const rosSpread = r.ros_spread != null && r.ros_spread >= ROS_CLOSE;
  return (
    <TableRow className="border-foreground/10 hover:bg-transparent">
      <TableCell className={`w-0 py-2 pr-0 pl-3 sm:pl-4 ${PIN_TILE}`}>
        <div className="size-9 overflow-hidden rounded-md sm:size-10">
          <TeamPanel abbr={r.team} playerId={r.player_id} name={r.name} muted={idle} tile />
        </div>
      </TableCell>
      {/* pinned at 11rem while the table scrolls; from `xl` it takes every pixel the numerals
          leave (w-full + max-w-0) and truncates */}
      <TableCell className={`py-2 whitespace-normal max-xl:w-[11rem] max-xl:min-w-[11rem] xl:w-full xl:max-w-0 ${PIN_NAME}`}>
        <p className={`flex flex-wrap items-center gap-x-2 gap-y-0.5 font-display text-[1.25rem] font-semibold leading-none tracking-tight ${idle ? "text-foreground/70" : ""}`}>
          <span className="truncate">{r.name}</span>
          {r.would_start && <Badge variant="start" shape="chip">start {signed(r.week_delta)}</Badge>}
          {hurt && (
            <Badge variant={TAG[injuryTone(r.injury)]} shape="chip" title={r.injury_note ?? undefined}>
              {injuryLabel(r.injury.replaceAll("_", " "), r.return_week, week)}
            </Badge>
          )}
          {r.bye && <Badge variant="muted" shape="chip">bye</Badge>}
        </p>
        <p className="mt-1 flex flex-wrap items-center gap-x-1 text-xs text-muted-foreground">
          <span className="font-semibold text-foreground/80">{r.pos}</span>
          <span aria-hidden>·</span>
          <span>{r.team}</span>
          <span aria-hidden>·</span>
          <Matchup r={r} />
        </p>
      </TableCell>
      <TableCell className={`text-right font-display text-[1.5rem] font-bold tabular-nums tracking-tight ${r.add > 0 ? "" : "text-foreground/70"}`}>
        {signed(r.add)}
      </TableCell>
      <TableCell className="pr-2 text-right align-middle">
        <p className={`font-display text-lg font-semibold tabular-nums tracking-tight ${r.week_proj === 0 ? "text-muted-foreground" : ""}`}>{pts(r.week_proj)}</p>
        {r.espn_only
          ? <Under>ESPN only</Under>
          : r.spread != null && r.spread >= 3
            ? <Under amber>±{pts(r.spread)}</Under>
            : null}
      </TableCell>
      {/* rest of season: ROS with its source spread beneath, then the two deltas */}
      <TableCell className={`${SIDE} ${GROUP_START}`} title={rosSources(r)}>
        {pts(r.ros_proj)}
        <Under amber={rosSpread}>{r.ros_spread != null ? `±${pts(r.ros_spread)}` : sourced(r) ? "1 src" : "season"}</Under>
      </TableCell>
      <TableCell className={SIDE}>{signed(r.delta_roster)}</TableCell>
      <TableCell className={`${SIDE} pr-2`}>{signed(r.delta_pool)}</TableCell>
      {/* his last three games, one column each */}
      {cols.map((col, i) => (
        <TableCell key={col.key} className={`${SIDE} ${i === 0 ? GROUP_START : ""} ${i === cols.length - 1 ? "pr-4" : ""}`}>
          <FormNumeral r={r} col={col} />
        </TableCell>
      ))}
    </TableRow>
  );
}

// The free-agent pool at one position as a board: a full-width card, the shadcn Table flush to
// its edges, one row per player with his team-colour tile. Rows arrive in the API's `add` order
// and open that way; any column heading re-sorts them (descending first, the numbers that
// matter being high ones; a second tap flips it), the name column alphabetically. The header
// is two rows: the stat-block headings (REST OF SEASON over ROS and the deltas, LAST 3 GAMES
// over the form columns), then the column labels; `add` and `wk` stand under no heading, they
// are the row's own numbers. Both header rows pin to the top and the tile and name to the
// left while the table scrolls.
export function WaiverTable({ rows, week, pos }: { rows: WaiverRow[]; week: number; pos: string }) {
  const [sort, setSort] = useState<Sort>({ key: "add", dir: "desc" });
  if (rows.length === 0) {
    return <p className="py-6 text-sm text-muted-foreground">ESPN lists no free agents at {pos} right now.</p>;
  }
  // only the form columns this position has: a kicker never has snaps or targets, a defence
  // no opportunity, and before the week's snaps land nobody has a snap share yet
  const cols = FORM_COLUMNS.filter((c) => rows.some((r) => r.form && formCell(r.form, c.key)));
  const onSort = (key: string) => setSort((s) => (
    s.key === key ? { key, dir: s.dir === "desc" ? "asc" : "desc" } : { key, dir: key === "name" ? "asc" : "desc" }));
  const head = (id: string, className: string, label: string, title?: string) => (
    <SortHead key={id} id={id} sort={sort} onSort={onSort} className={className} title={title}>{label}</SortHead>
  );
  // the card clips its corners with overflow-hidden, which would also stop the headers pinning
  // to the page from `xl`; there the card lets them out and the header row rounds its own
  // top corners instead
  return (
    <Card className="rounded-lg py-0 shadow-[0_10px_28px_-16px_rgb(0_0_0_/_0.55)] [--card-spacing:--spacing(3)] sm:[--card-spacing:--spacing(4)] xl:overflow-visible">
      <CardContent className="px-0 sm:px-0">
        <Table containerClassName={PANEL}>
          <TableHeader>
            <TableRow className="border-0 hover:bg-transparent">
              <TableHead colSpan={2} className={`h-7 rounded-tl-lg border-0 ${PIN_TOP_1} ${PIN_TILE} ${PIN_CORNER}`} />
              <TableHead colSpan={2} className={`h-7 border-0 ${PIN_TOP_1}`} />
              <TableHead colSpan={3} className={`h-7 ${GROUP} ${PIN_TOP_1}`}>Rest of season</TableHead>
              <TableHead colSpan={cols.length} className={`h-7 rounded-tr-lg ${GROUP} ${PIN_TOP_1}`} title="his last three games played, within the last five weeks; none of it is in add">Last 3 games</TableHead>
            </TableRow>
            <TableRow className="border-foreground/10 hover:bg-transparent">
              <TableHead className={`h-9 pl-3 sm:pl-4 ${PIN_TOP_2} ${PIN_TILE} ${PIN_CORNER}`}><span className="sr-only">Team</span></TableHead>
              {head("name", `${PIN_TOP_2} ${PIN_NAME} ${PIN_CORNER} max-xl:border-r-0`, "Player")}
              {head("add", `text-right ${PIN_TOP_2}`, "add", "projected points gained this season by adding him")}
              {head("wk", `text-right ${PIN_TOP_2}`, "wk", "this week's projection")}
              {head("ros", `text-right ${GROUP_START} ${PIN_TOP_2}`, "ROS", "Sleeper's remaining weeks and CBS's rest-of-season projection, averaged; the gap between them beneath")}
              {head("dr", `text-right ${PIN_TOP_2}`, "Δ roster", "ROS against my weakest at the position")}
              {head("dp", `text-right ${PIN_TOP_2}`, "Δ pool", "ROS against the league's replacement level")}
              {cols.map((col, i) => head(col.key, `text-right ${PIN_TOP_2} ${i === 0 ? GROUP_START : ""} ${i === cols.length - 1 ? "pr-2" : ""}`, col.label, col.title))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {sortRows(rows, sort).map((r) => <Row key={r.player_id} r={r} week={week} cols={cols} />)}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
