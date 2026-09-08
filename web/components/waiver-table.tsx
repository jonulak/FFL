import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { WaiverRow } from "@/lib/api";
import { injuryTone, pts, signed } from "@/lib/format";

// Rows are rendered in the order the API returns them (by `add`); never re-sort here.
export function WaiverTable({ rows }: { rows: WaiverRow[] }) {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Player</TableHead>
            <TableHead className="text-right">add</TableHead>
            <TableHead className="text-right">wk</TableHead>
            <TableHead className="text-right">ROS</TableHead>
            <TableHead className="text-right">Δ roster</TableHead>
            <TableHead className="text-right">Δ pool</TableHead>
            <TableHead className="text-right">starts?</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.player_id}>
              <TableCell>
                <div className="flex flex-wrap items-center gap-1.5 font-medium">
                  <span className="whitespace-nowrap">{r.name}</span>
                  {r.injury !== "ACTIVE" && (
                    <Badge variant={injuryTone(r.injury) === "bad" ? "destructive" : "outline"}>
                      {r.injury}
                    </Badge>
                  )}
                </div>
                <div className="text-xs text-muted-foreground whitespace-nowrap">
                  {r.pos} · {r.team}
                  {r.bye ? " · BYE" : ""}
                  {r.espn_only
                    ? " · ESPN only"
                    : r.spread != null && r.spread >= 3
                      ? ` · ±${pts(r.spread)}`
                      : ""}
                </div>
              </TableCell>
              <TableCell className="text-right font-semibold tabular-nums">{signed(r.add)}</TableCell>
              <TableCell className="text-right tabular-nums">{pts(r.week_proj)}</TableCell>
              <TableCell className="text-right tabular-nums">{pts(r.ros_proj)}</TableCell>
              <TableCell className="text-right tabular-nums">{signed(r.delta_roster)}</TableCell>
              <TableCell className="text-right tabular-nums">{signed(r.delta_pool)}</TableCell>
              <TableCell className="text-right tabular-nums">
                {r.would_start ? signed(r.week_delta) : "—"}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
