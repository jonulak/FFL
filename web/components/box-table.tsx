import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { BoxItem } from "@/lib/api";
import { boxSum, count, points, rule } from "@/lib/box";

const HEAD = "h-auto px-0 pb-1.5 font-display text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground";

// One game's points taken apart: every stat the league scores, its count, the rate it is scored
// at and what it earned, ruled off to the total. Shared by the Matchup breakdown and every week
// of the season modal, so a week reads the same wherever it is opened.
export function BoxTable({ box }: { box: BoxItem[] }) {
  if (box.length === 0) return null;
  return (
    <Table className="tabular-nums">
      <TableHeader>
        <TableRow className="border-foreground/10 hover:bg-transparent">
          <TableHead className={`${HEAD} text-left`}>Stat</TableHead>
          <TableHead className={`${HEAD} text-right`}>Count</TableHead>
          <TableHead className={`${HEAD} pl-3 text-right`}>Scoring</TableHead>
          <TableHead className={`${HEAD} pl-3 text-right`}>Pts</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {box.map((b) => (
          <TableRow key={b.stat_id} className="border-foreground/10 hover:bg-transparent">
            <TableHead scope="row" className="h-auto px-0 py-2 pr-2 font-normal leading-snug whitespace-normal">{b.label}</TableHead>
            <TableCell className="px-0 py-2 text-right">{count(b.count)}</TableCell>
            <TableCell className="px-0 py-2 pl-3 text-right text-muted-foreground">{rule(b)}</TableCell>
            <TableCell className={`px-0 py-2 pl-3 text-right font-medium ${b.points < 0 ? "text-out" : ""}`}>{points(b.points)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
      <TableFooter className="border-t-2 border-foreground/35 bg-transparent font-normal">
        <TableRow className="hover:bg-transparent">
          <TableHead scope="row" colSpan={3} className={`${HEAD} pt-2.5 pb-0`}>Total</TableHead>
          <TableCell className="px-0 pt-2 pb-0 pl-3 text-right font-display text-[1.5rem] font-bold leading-none tracking-tight">{points(boxSum(box))}</TableCell>
        </TableRow>
      </TableFooter>
    </Table>
  );
}
