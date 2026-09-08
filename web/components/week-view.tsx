import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { Player, Side, Week } from "@/lib/api";
import { injuryTone, pts, signed } from "@/lib/format";
import { StaleBadge } from "./stale-badge";

const TONE = { ok: "secondary", warn: "outline", bad: "destructive" } as const;

function Row({ p }: { p: Player }) {
  return (
    <TableRow className={p.bye || injuryTone(p.injury) === "bad" ? "opacity-60" : ""}>
      <TableCell className="font-mono text-xs">{p.slot}</TableCell>
      <TableCell>
        <div className="font-medium">{p.name}</div>
        <div className="text-xs text-muted-foreground">
          {p.pos} · {p.team}
          {p.bye ? " · BYE" : ""}
        </div>
      </TableCell>
      <TableCell>
        {p.injury !== "ACTIVE" && <Badge variant={TONE[injuryTone(p.injury)]}>{p.injury}</Badge>}
      </TableCell>
      <TableCell className="text-right tabular-nums">
        {pts(p.proj)}
        {p.spread != null && p.spread >= 3 && (
          <span className="ml-1 text-xs text-muted-foreground">±{pts(p.spread)}</span>
        )}
      </TableCell>
      <TableCell className="text-right tabular-nums">
        {p.game_played || p.actual ? pts(p.actual) : "—"}
      </TableCell>
    </TableRow>
  );
}

function SideCard({ side, title }: { side: Side; title: string }) {
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-baseline justify-between gap-x-2">
        <CardTitle className="text-base">
          {title}: {side.team}
        </CardTitle>
        <div className="text-sm tabular-nums">
          proj {pts(side.proj_total)} · actual {pts(side.actual_total)}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Slot</TableHead>
              <TableHead>Player</TableHead>
              <TableHead />
              <TableHead className="text-right">Proj</TableHead>
              <TableHead className="text-right">Pts</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {side.lineup.map((p) => <Row key={p.player_id} p={p} />)}
            {side.bench.map((p) => <Row key={p.player_id} p={p} />)}
            {side.ir.map((p) => <Row key={p.player_id} p={p} />)}
          </TableBody>
        </Table>
        {side.optimal_swaps.length > 0 && (
          <div className="rounded border p-2 text-sm">
            <div className="mb-1 font-medium">
              By projection ({signed(side.optimal_total - side.proj_total)})
            </div>
            <ul className="space-y-0.5">
              {side.optimal_swaps.map((s, i) => (
                <li key={i} className="font-mono text-xs break-words">
                  {s.slot}: {s.out} → {s.in} ({signed(s.gain)})
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function WeekView({ data }: { data: Week }) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-xl font-semibold">Week {data.week}</h1>
        {data.live && <Badge>live</Badge>}
        <StaleBadge since={data.stale_since} />
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <SideCard side={data.me} title="Me" />
        {data.opp && <SideCard side={data.opp} title="Opponent" />}
      </div>
    </div>
  );
}
