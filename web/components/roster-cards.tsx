import { Badge } from "@/components/ui/badge";
import { Card as UiCard, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Card } from "@/lib/api";
import { injuryLabel, injuryTone, pts } from "@/lib/format";
import { Ago, Kickoff } from "./local-time";

const TONE = { ok: "secondary", warn: "outline", bad: "destructive" } as const;
const CALL_VARIANT = { START: "default", BENCH: "secondary", OUT: "destructive", BYE: "outline", IR: "outline" } as const;
const GROUPS: { key: Card["group"]; title: string }[] = [
  { key: "starter", title: "Starters" }, { key: "bench", title: "Bench" }, { key: "ir", title: "IR" },
];

function PlayerCard({ c, week }: { c: Card; week: number }) {
  return (
    <UiCard className={c.call === "BYE" || c.call === "OUT" ? "opacity-70" : ""}>
      <CardHeader className="flex flex-row flex-wrap items-baseline justify-between gap-x-2 pb-2">
        <div>
          <CardTitle className="text-base">{c.name}</CardTitle>
          <div className="text-xs text-muted-foreground">
            {c.pos} · {c.team} · {c.opponent ? `vs ${c.opponent} · ` : ""}<Kickoff iso={c.kickoff} />
          </div>
        </div>
        <div className="flex items-center gap-1">
          {c.close_call && <Badge variant="outline">close call</Badge>}
          <Badge variant={CALL_VARIANT[c.call]}>{c.call}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <div className="flex flex-wrap items-center gap-2 tabular-nums">
          <span>proj {pts(c.proj)}</span>
          {c.spread != null && c.spread >= 3 && <span className="text-xs text-muted-foreground">±{pts(c.spread)}</span>}
          {c.injury !== "ACTIVE" && (
            <Badge variant={TONE[injuryTone(c.injury)]} title={c.injury_note ?? undefined}>
              {injuryLabel(c.injury, c.return_week, week)}
            </Badge>
          )}
          {(c.game_played > 0 || c.actual > 0) && <span>· actual {pts(c.actual)}</span>}
        </div>
        <p className="font-medium">{c.reason}</p>
        {c.outlook ? (
          <div>
            <p className="text-muted-foreground">{c.outlook}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              outlook · {c.outlook_at ? <Ago iso={c.outlook_at} /> : "—"}
              {c.outlook_stale ? " · facts changed since" : ""}
            </p>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">no outlook yet</p>
        )}
      </CardContent>
    </UiCard>
  );
}

export function RosterCards({ cards, week }: { cards: Card[]; week: number }) {
  return (
    <div className="space-y-6">
      {GROUPS.map(({ key, title }) => {
        const rows = cards.filter((c) => c.group === key);
        if (rows.length === 0) return null;
        return (
          <section key={key} className="space-y-2">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">{title}</h2>
            <div className="grid gap-3 sm:grid-cols-2">
              {rows.map((c) => <PlayerCard key={c.player_id} c={c} week={week} />)}
            </div>
          </section>
        );
      })}
    </div>
  );
}
