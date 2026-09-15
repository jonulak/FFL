import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { WaiverRow } from "@/lib/api";
import { injuryLabel, injuryTone, pts, signed } from "@/lib/format";
import { Kickoff } from "./local-time";
import { TeamLogo, TeamPanel } from "./player-media";

const TAG = { ok: "secondary", warn: "warn", bad: "out" } as const;
// ESPN says ACTIVE for a player and NORMAL for a defence; neither is news.
const HEALTHY = new Set(["ACTIVE", "NORMAL"]);
const LABEL = "font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground";
// The secondary numerals (ROS and the two deltas): condensed, stepped back, the way the League
// table sets points against. Below `sm` these columns fold into the caption under the name.
const SIDE = "hidden text-right font-display text-lg font-medium tabular-nums tracking-tight text-foreground/70 sm:table-cell sm:px-3";
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

// One free agent, one row: the Matchup page's lineup row with the waiver numbers beside it.
// `add` is the decision column and the only bold numeral; a non-positive add steps back to 70%
// ink so the one worth a claim, when there is one, reads first. A player who would go straight
// into this week's optimal lineup carries the START chip with the lineup gain — the same
// meaning lime has everywhere else on the board.
function Row({ r, week }: { r: WaiverRow; week: number }) {
  const hurt = !HEALTHY.has(r.injury);
  const idle = r.bye || injuryTone(r.injury) === "bad";
  return (
    <TableRow className="border-foreground/10 hover:bg-transparent">
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
        {/* the phone has no room for the ROS and delta columns; they stay visible here instead */}
        <p className="mt-0.5 flex flex-wrap gap-x-1 text-xs tabular-nums text-muted-foreground sm:hidden">
          <span className="whitespace-nowrap" title={rosSources(r)}>ROS {pts(r.ros_proj)}{r.ros_spread != null && ` ±${pts(r.ros_spread)}`}</span>
          <span aria-hidden>·</span>
          <span className="whitespace-nowrap">Δ roster {signed(r.delta_roster)}</span>
          <span aria-hidden>·</span>
          <span className="whitespace-nowrap">Δ pool {signed(r.delta_pool)}</span>
        </p>
      </TableCell>
      <TableCell className={`text-right font-display text-[1.5rem] font-bold tabular-nums tracking-tight ${r.add > 0 ? "" : "text-foreground/70"}`}>
        {signed(r.add)}
      </TableCell>
      <TableCell className="pr-3 text-right align-middle sm:pr-2">
        <p className={`font-display text-lg font-semibold tabular-nums tracking-tight ${r.week_proj === 0 ? "text-muted-foreground" : ""}`}>{pts(r.week_proj)}</p>
        {r.espn_only
          ? <p className="text-xs text-muted-foreground">ESPN only</p>
          : r.spread != null && r.spread >= 3
            ? <p className="text-xs font-medium tabular-nums text-close-foreground dark:text-close">±{pts(r.spread)}</p>
            : null}
      </TableCell>
      <TableCell className={SIDE} title={rosSources(r)}>{pts(r.ros_proj)}</TableCell>
      <TableCell className={`${SIDE} ${r.ros_spread != null && r.ros_spread >= ROS_CLOSE ? "text-close-foreground dark:text-close" : "text-muted-foreground"}`}>
        {r.ros_spread != null ? `±${pts(r.ros_spread)}` : sourced(r) ? "1 src" : "season"}
      </TableCell>
      <TableCell className={SIDE}>{signed(r.delta_roster)}</TableCell>
      <TableCell className={`${SIDE} pr-3 sm:pr-4`}>{signed(r.delta_pool)}</TableCell>
    </TableRow>
  );
}

// The free-agent pool at one position as a board: a full-width card, the shadcn Table flush to
// its edges, one row per player with his team-colour tile. Rows are rendered in the order the
// API returns them (by `add`); never re-sort here.
export function WaiverTable({ rows, week, pos }: { rows: WaiverRow[]; week: number; pos: string }) {
  if (rows.length === 0) {
    return <p className="py-6 text-sm text-muted-foreground">ESPN lists no free agents at {pos} right now.</p>;
  }
  return (
    <Card className="rounded-lg py-0 shadow-[0_10px_28px_-16px_rgb(0_0_0_/_0.55)] [--card-spacing:--spacing(3)] sm:[--card-spacing:--spacing(4)]">
      <CardContent className="px-0 sm:px-0">
        <Table>
          <TableHeader>
            <TableRow className="border-foreground/10 hover:bg-transparent">
              <TableHead className="h-9 pl-3 sm:pl-4"><span className="sr-only">Team</span></TableHead>
              <TableHead className={`${LABEL} h-9`}>Player</TableHead>
              <TableHead className={`${LABEL} h-9 text-right`} aria-sort="descending">add</TableHead>
              <TableHead className={`${LABEL} h-9 pr-3 text-right sm:pr-2`}>wk</TableHead>
              <TableHead className={`${LABEL} hidden h-9 text-right sm:table-cell`}>ROS</TableHead>
              <TableHead className={`${LABEL} hidden h-9 text-right sm:table-cell`} title="gap between Sleeper's remaining weeks and CBS's rest-of-season projection">±</TableHead>
              <TableHead className={`${LABEL} hidden h-9 text-right sm:table-cell`}>Δ roster</TableHead>
              <TableHead className={`${LABEL} hidden h-9 pr-3 text-right sm:table-cell sm:pr-4`}>Δ pool</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => <Row key={r.player_id} r={r} week={week} />)}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
