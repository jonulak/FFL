import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { WaiverRow } from "@/lib/api";
import { FORM_COLUMNS, formCell, injuryLabel, injuryTone, pts, signed } from "@/lib/format";
import { FactStrip, FormStrip, TrendArrow } from "./form-strip";
import { Kickoff } from "./local-time";
import { TeamLogo, TeamPanel } from "./player-media";

const TAG = { ok: "secondary", warn: "warn", bad: "out" } as const;
// ESPN says ACTIVE for a player and NORMAL for a defence; neither is news.
const HEALTHY = new Set(["ACTIVE", "NORMAL"]);
const LABEL = "font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground";
// The stat-block headings over the two groups of secondary columns: the label style a step
// smaller and lighter, with the hairline under it running the width of its group only.
const GROUP = "h-7 border-b border-foreground/12 pb-0 pl-4 pr-2 align-bottom font-display text-[0.6875rem] font-semibold uppercase tracking-[0.14em] text-foreground/55";
// The secondary numerals (ROS, the two deltas, the seven form columns): condensed, stepped back,
// the way the League table sets points against. Right-aligned, 8px gutters, so twelve of them
// fit beside the name at the `lg` container width. The first column of each group is set off
// from the last of the one before by a 16px gutter and a hairline.
const SIDE = "text-right font-display text-lg font-medium tabular-nums tracking-tight text-foreground/70";
const GROUP_START = "border-l border-foreground/10 pl-4";
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

// One of the seven form columns for one player. A column his position has no number for, or
// that no game in the window has yet, is an empty cell: the grid says it, a dash would only add
// ink. The opportunity cell carries the trend as a drawn arrow after the number.
type FormCol = (typeof FORM_COLUMNS)[number];

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

// One free agent, one row: the Matchup page's lineup row with the waiver numbers beside it.
// `add` is the decision column and the only bold numeral; a non-positive add steps back to 70%
// ink so the one worth a claim, when there is one, reads first. A player who would go straight
// into this week's optimal lineup carries the START chip with the lineup gain — the same
// meaning lime has everywhere else on the board. Two stat blocks follow: the rest of season
// (ROS and the two deltas, from `sm`) and his last three games (from `lg`); below those widths
// the same figures fold into caption lines under the name, so nothing is hidden on a phone.
function Row({ r, week, cols }: { r: WaiverRow; week: number; cols: FormCol[] }) {
  const hurt = !HEALTHY.has(r.injury);
  const idle = r.bye || injuryTone(r.injury) === "bad";
  const rosSpread = r.ros_spread != null && r.ros_spread >= ROS_CLOSE;
  return (
    <>
    {/* on the phone this row's rule moves to the strip row beneath it: the two are one player */}
    <TableRow className="border-foreground/10 hover:bg-transparent max-sm:border-0">
      <TableCell className="w-0 py-2 pr-0 pl-3 sm:pl-4">
        <div className="size-9 overflow-hidden rounded-md sm:size-10">
          <TeamPanel abbr={r.team} playerId={r.player_id} name={r.name} muted={idle} tile />
        </div>
      </TableCell>
      {/* w-full + max-w-0: the name column takes every pixel the numerals leave, and truncates */}
      <TableCell className="w-full max-w-0 py-2 whitespace-normal">
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
        {/* between `sm` and `lg` the name column is wide and the form columns are not there yet:
            the form strip runs as a third caption line. On the phone it moves to its own row. */}
        <div className="mt-0.5 hidden sm:block lg:hidden"><FormStrip form={r.form} /></div>
      </TableCell>
      <TableCell className={`text-right font-display text-[1.5rem] font-bold tabular-nums tracking-tight ${r.add > 0 ? "" : "text-foreground/70"}`}>
        {signed(r.add)}
      </TableCell>
      <TableCell className="pr-3 text-right align-middle sm:pr-2">
        <p className={`font-display text-lg font-semibold tabular-nums tracking-tight ${r.week_proj === 0 ? "text-muted-foreground" : ""}`}>{pts(r.week_proj)}</p>
        {r.espn_only
          ? <Under>ESPN only</Under>
          : r.spread != null && r.spread >= 3
            ? <Under amber>±{pts(r.spread)}</Under>
            : null}
      </TableCell>
      {/* rest of season: ROS with its source spread beneath, then the two deltas */}
      <TableCell className={`hidden sm:table-cell ${SIDE} ${GROUP_START}`} title={rosSources(r)}>
        {pts(r.ros_proj)}
        <Under amber={rosSpread}>{r.ros_spread != null ? `±${pts(r.ros_spread)}` : sourced(r) ? "1 src" : "season"}</Under>
      </TableCell>
      <TableCell className={`hidden sm:table-cell ${SIDE}`}>{signed(r.delta_roster)}</TableCell>
      <TableCell className={`hidden pr-3 sm:table-cell sm:pr-4 lg:pr-2 ${SIDE}`}>{signed(r.delta_pool)}</TableCell>
      {/* his last three games, one column each */}
      {cols.map((col, i) => (
        <TableCell key={col.key} className={`hidden lg:table-cell ${SIDE} ${i === 0 ? GROUP_START : ""} ${i === cols.length - 1 ? "pr-4" : ""}`}>
          <FormNumeral r={r} col={col} />
        </TableCell>
      ))}
    </TableRow>
    {/* the phone has no room for the ROS and form columns beside the name: the same figures run
        as two strips across the row's full width beneath him, so nothing is hidden and the
        identity row above stays three lines */}
    <TableRow className="border-foreground/10 hover:bg-transparent sm:hidden">
      <TableCell colSpan={4} className="whitespace-normal pt-0 pb-2.5 pl-3 pr-3">
        <FactStrip items={[
          { key: "ros", label: "ROS", value: `${pts(r.ros_proj)}${r.ros_spread != null ? ` ±${pts(r.ros_spread)}` : ""}`, title: rosSources(r) },
          { key: "dr", label: "Δ roster", value: signed(r.delta_roster), title: "ROS against my weakest at the position" },
          { key: "dp", label: "Δ pool", value: signed(r.delta_pool), title: "ROS against the league's replacement level" },
        ]} />
        <FormStrip form={r.form} className="mt-0.5" />
      </TableCell>
    </TableRow>
    </>
  );
}

// The free-agent pool at one position as a board: a full-width card, the shadcn Table flush to
// its edges, one row per player with his team-colour tile. Rows are rendered in the order the
// API returns them (by `add`); never re-sort here. The header is two rows: the stat-block
// headings (REST OF SEASON over ROS and the deltas, LAST 3 GAMES over the form columns), then
// the column labels; `add` and `wk` stand under no heading, they are the row's own numbers.
export function WaiverTable({ rows, week, pos }: { rows: WaiverRow[]; week: number; pos: string }) {
  if (rows.length === 0) {
    return <p className="py-6 text-sm text-muted-foreground">ESPN lists no free agents at {pos} right now.</p>;
  }
  // only the form columns this position has: a kicker never has snaps or targets, a defence
  // no opportunity, and before the week's snaps land nobody has a snap share yet
  const cols = FORM_COLUMNS.filter((c) => rows.some((r) => r.form && formCell(r.form, c.key)));
  return (
    <Card className="rounded-lg py-0 shadow-[0_10px_28px_-16px_rgb(0_0_0_/_0.55)] [--card-spacing:--spacing(3)] sm:[--card-spacing:--spacing(4)]">
      <CardContent className="px-0 sm:px-0">
        <Table>
          <TableHeader>
            <TableRow className="hidden border-0 hover:bg-transparent sm:table-row">
              <TableHead colSpan={4} className="h-7 border-0" />
              <TableHead colSpan={3} className={`${GROUP} hidden sm:table-cell`}>Rest of season</TableHead>
              <TableHead colSpan={cols.length} className={`${GROUP} hidden lg:table-cell`} title="his last three games played, within the last five weeks; none of it is in add">Last 3 games</TableHead>
            </TableRow>
            <TableRow className="border-foreground/10 hover:bg-transparent">
              <TableHead className="h-9 pl-3 sm:pl-4"><span className="sr-only">Team</span></TableHead>
              <TableHead className={`${LABEL} h-9`}>Player</TableHead>
              <TableHead className={`${LABEL} h-9 text-right`} aria-sort="descending">add</TableHead>
              <TableHead className={`${LABEL} h-9 pr-3 text-right sm:pr-2`}>wk</TableHead>
              <TableHead className={`${LABEL} hidden h-9 text-right sm:table-cell ${GROUP_START}`} title="Sleeper's remaining weeks and CBS's rest-of-season projection, averaged; the gap between them beneath">ROS</TableHead>
              <TableHead className={`${LABEL} hidden h-9 text-right sm:table-cell`} title="ROS against my weakest at the position">Δ roster</TableHead>
              <TableHead className={`${LABEL} hidden h-9 pr-3 text-right sm:table-cell sm:pr-4 lg:pr-2`} title="ROS against the league's replacement level">Δ pool</TableHead>
              {cols.map((col, i) => (
                <TableHead key={col.key} title={col.title}
                  className={`${LABEL} hidden h-9 text-right lg:table-cell ${i === 0 ? GROUP_START : ""} ${i === cols.length - 1 ? "pr-4" : ""}`}>
                  {col.label}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => <Row key={r.player_id} r={r} week={week} cols={cols} />)}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
