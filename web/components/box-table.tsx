import type { BoxItem } from "@/lib/api";
import { boxSum, count, points, rule } from "@/lib/box";

const HEAD = "pb-1.5 font-display text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground";

// One game's points taken apart: every stat the league scores, its count, the rate it is scored
// at and what it earned, ruled off to the total. Shared by the Matchup breakdown and every week
// of the season modal, so a week reads the same wherever it is opened.
export function BoxTable({ box }: { box: BoxItem[] }) {
  if (box.length === 0) return null;
  return (
    <table className="w-full text-sm tabular-nums">
      <thead>
        <tr className="border-b border-foreground/10">
          <th scope="col" className={`${HEAD} text-left`}>Stat</th>
          <th scope="col" className={`${HEAD} text-right`}>Count</th>
          <th scope="col" className={`${HEAD} pl-3 text-right`}>Scoring</th>
          <th scope="col" className={`${HEAD} pl-3 text-right`}>Pts</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-foreground/10">
        {box.map((b) => (
          <tr key={b.stat_id}>
            <th scope="row" className="py-2 pr-2 text-left font-normal leading-snug">{b.label}</th>
            <td className="py-2 text-right">{count(b.count)}</td>
            <td className="py-2 pl-3 text-right whitespace-nowrap text-muted-foreground">{rule(b)}</td>
            <td className={`py-2 pl-3 text-right font-medium ${b.points < 0 ? "text-out" : ""}`}>{points(b.points)}</td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr className="border-t-2 border-foreground/35">
          <th scope="row" colSpan={3} className="pt-2.5 text-left font-display text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">Total</th>
          <td className="pt-2 pl-3 text-right font-display text-[1.5rem] font-bold leading-none tracking-tight">{points(boxSum(box))}</td>
        </tr>
      </tfoot>
    </table>
  );
}
