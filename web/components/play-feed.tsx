import Link from "next/link";
import type { Play } from "@/lib/api";
import { pts, signed } from "@/lib/format";
import { Clock } from "./local-time";

// The play feed: every points change of a starter in the matchup, newest first. My rows hang
// on the left, theirs on the right, so the column a row sits in says whose it is before the
// name is read. The delta is the fact of the row; the running total sits beside it in the
// small unit, the play text beneath, the game clock as a caption.
export function PlayFeed({ plays, full = false }: { plays: Play[]; full?: boolean }) {
  if (plays.length === 0) {
    return <p className="py-3 text-center text-xs text-muted-foreground">No plays yet.</p>;
  }
  return (
    <ol className={`space-y-2 ${full ? "" : ""}`}>
      {plays.map((p) => <PlayRow key={`${p.at}-${p.player_id}-${p.total}`} p={p} />)}
    </ol>
  );
}

function PlayRow({ p }: { p: Play }) {
  const mine = p.side === "me";
  const up = p.delta > 0;
  return (
    <li className={`flex ${mine ? "justify-start" : "justify-end"}`}>
      <div className={`w-full max-w-[85%] rounded-md border bg-card/60 px-3 py-2 sm:max-w-[70%] ${mine ? "text-left" : "text-right"}`}>
        <div className={`flex flex-wrap items-baseline gap-x-2 gap-y-0.5 ${mine ? "" : "flex-row-reverse"}`}>
          <span className="font-display text-[15px] font-semibold leading-tight">{p.name}</span>
          <span className="font-display text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{p.pos} · {p.team}</span>
          <span className={`ml-auto font-display text-[15px] font-bold tabular-nums ${up ? "text-foreground" : "text-destructive"} ${mine ? "" : "ml-0 mr-auto"}`}>
            {signed(p.delta)}
          </span>
          <span className="text-xs tabular-nums text-muted-foreground">{pts(p.total)} <span className="font-display font-semibold uppercase tracking-[0.12em]">pts</span></span>
        </div>
        {p.text && <p className="mt-0.5 text-[13px] leading-snug text-foreground/85">{p.text}</p>}
        <p className="mt-0.5 text-[11px] tabular-nums text-muted-foreground">
          <Clock iso={p.at} />
          {p.quarter != null && p.clock && <> <span aria-hidden>·</span> Q{p.quarter} {p.clock}</>}
        </p>
      </div>
    </li>
  );
}

// The strip on the Matchup page: the latest few rows and the way to the whole day.
export function PlayFeedStrip({ plays }: { plays: Play[] }) {
  return (
    <section aria-label="Latest plays" className="space-y-2">
      <div className="flex items-baseline justify-between">
        <h2 className="font-display text-sm font-semibold uppercase tracking-[0.14em] text-muted-foreground">Latest plays</h2>
        <Link href="/plays" className="text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
          All plays <span aria-hidden>→</span>
        </Link>
      </div>
      <PlayFeed plays={plays} />
    </section>
  );
}
