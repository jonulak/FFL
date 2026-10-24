import { Badge } from "@/components/ui/badge";

// A fantasy playoff week, as the league's settings name them (settings.json `playoff_weeks`, off ESPN's
// schedule): the same quiet chip in the week pickers and beside them. `short` is the player log's "PO":
// its week column is one table-wide, and the full word widened it enough to squeeze every row.
export function PlayoffChip({ short = false, className = "" }: { short?: boolean; className?: string }) {
  return (
    <Badge variant="muted" shape="chip" className={className}>
      {short ? <><span aria-hidden>PO</span><span className="sr-only">Playoffs</span></> : "Playoffs"}
    </Badge>
  );
}
