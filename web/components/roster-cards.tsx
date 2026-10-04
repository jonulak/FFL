import { Badge } from "@/components/ui/badge";
import { Card as UiCard, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import type { Card } from "@/lib/api";
import { injuryLabel, injuryTone, pts } from "@/lib/format";
import { team } from "@/lib/teams";
import { FormStrip } from "./form-strip";
import { Kickoff } from "./local-time";
import { Outlook } from "./outlook";
import { NAME_LINK } from "@/lib/player-log";
import { PlayerTrigger } from "./player-dialog";
import { TeamLogo, TeamPanel } from "./player-media";
import { StatLine } from "./stat-line";

const CALL = { START: "start", BENCH: "muted", OUT: "out", BYE: "muted", IR: "out" } as const;
const TAG = { ok: "secondary", warn: "warn", bad: "out" } as const;
// ESPN says ACTIVE for a player and NORMAL for a defence; neither is news.
const HEALTHY = new Set(["ACTIVE", "NORMAL"]);
const GROUPS: { key: Card["group"]; title: string }[] = [
  { key: "starter", title: "Starters" }, { key: "bench", title: "Bench" }, { key: "ir", title: "IR" },
];

// "at LAR" when he travels, "vs SF" at home; without a venue the opponent still shows, bare.
function Matchup({ c }: { c: Card }) {
  if (!c.opponent) {
    return <span className="text-sm text-muted-foreground"><Kickoff iso={c.kickoff} bye={c.bye} game={c.game} team={c.team} /></span>;
  }
  return (
    <span className="flex flex-wrap items-center gap-x-1.5 text-sm text-muted-foreground">
      {c.home !== null && <span className="font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-foreground/80">{c.home ? "vs" : "at"}</span>}
      <TeamLogo abbr={c.opponent} size={20} />
      <span className="font-medium text-foreground/85">{c.opponent}</span>
      <span aria-hidden>·</span>
      <Kickoff iso={c.kickoff} bye={c.bye} game={c.game} team={c.team} />
    </span>
  );
}

// The number that decides the call. Once the game is on, the score he has posted takes the big
// slot and the projection steps beside it.
function Numbers({ c }: { c: Card }) {
  const live = c.game_played > 0 || c.actual > 0;
  const big = live ? c.actual : c.proj;
  const zero = !live && c.proj === 0;
  return (
    <div className="flex flex-wrap items-end gap-x-3 gap-y-1">
      <div className="flex items-baseline gap-1.5">
        <span className={`font-display text-[2.6rem] font-bold leading-none tracking-tight ${zero ? "text-muted-foreground" : ""}`}>
          {pts(big)}
        </span>
        <span className="font-display text-sm font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          {live ? "pts" : "proj"}
        </span>
      </div>
      <div className="pb-1 text-xs tabular-nums text-muted-foreground">
        {live && <span className="mr-2">proj {pts(c.proj)}</span>}
        {c.espn_only
          ? <span>ESPN only</span>
          : <span>ESPN {pts(c.proj_espn)} <span aria-hidden>·</span> Sleeper {pts(c.proj_sleeper)}</span>}
        {c.spread != null && c.spread >= 3 && (
          <span className="ml-2 font-medium text-close-foreground dark:text-close">±{pts(c.spread)}</span>
        )}
      </div>
      <Ros c={c} />
    </div>
  );
}

// The two ROS sources disagreeing by this much over the rest of the season is worth a look — the
// weekly 3-point rule scaled to the weeks left, as on the Waivers page.
const ROS_CLOSE = 20;

// Rest of season under the week's numbers: the mean, with the sources on hover and the gap between
// them when both have him. Both sources are revised in season; the frozen draft-day total is not shown.
function Ros({ c }: { c: Card }) {
  if (c.ros_proj == null) return null;
  const parts = [];
  if (c.ros_sleeper != null) parts.push(`Sleeper (Rotowire) weeks left ${pts(c.ros_sleeper)}`);
  if (c.ros_cbs != null) parts.push(`CBS rest of season ${pts(c.ros_cbs)}`);
  const title = parts.length ? parts.join(" · ") : "no rest-of-season source has him: season projection scaled by the weeks left";
  return (
    <div className="basis-full text-xs tabular-nums text-muted-foreground" title={title}>
      <span className="font-display text-[0.6875rem] font-semibold uppercase tracking-[0.12em]">ROS</span>{" "}
      <span className="font-medium text-foreground/70">{pts(c.ros_proj)}</span>
      {c.ros_spread != null && (
        <span className={`ml-2 ${c.ros_spread >= ROS_CLOSE ? "font-medium text-close-foreground dark:text-close" : ""}`}>±{pts(c.ros_spread)}</span>
      )}
      {!parts.length && <span className="ml-2">season</span>}
    </div>
  );
}

// The rule that fired, on one line at laptop widths. season.roster_cards appends
// " — his status is your call" for QUESTIONABLE/DOUBTFUL; that note gets its own small line so
// the call and the gap never wrap. On the phone the column is too narrow to keep the alternative
// on one line, so it wraps there rather than hide who he is being compared to.
function Reason({ text }: { text: string }) {
  const [main, note] = text.split(" — ", 2);
  return (
    <div className="mt-2">
      <p className="text-[0.9375rem] font-medium leading-snug md:truncate" title={main}>{main}</p>
      {note && <p className="mt-0.5 text-xs text-muted-foreground">{note}</p>}
    </div>
  );
}

// shadcn Card, laid out as a broadcast lineup panel: the team-colour media column spans every
// row on the left, header / content / footer stack on the right.
function PlayerCard({ c, week, runBusy }: { c: Card; week: number; runBusy: boolean }) {
  const t = team(c.team);
  const idle = c.call === "BYE" || c.call === "OUT" || c.call === "IR";
  // An IR or OUT call is the injury status restated; the tag alone carries it, with the return week.
  const hurt = !HEALTHY.has(c.injury);
  const showCall = !(hurt && (c.call === "IR" || c.call === "OUT"));
  return (
    <UiCard
      className="grid grid-cols-[5.25rem_minmax(0,1fr)] grid-rows-[auto_auto_1fr] gap-0 rounded-lg py-0 shadow-[0_10px_28px_-16px_rgb(0_0_0_/_0.55)] sm:grid-cols-[6.25rem_minmax(0,1fr)] [--card-spacing:--spacing(3)] sm:[--card-spacing:--spacing(4)]"
      aria-label={`${c.name}, ${c.call}`}
    >
      <PlayerTrigger p={c} className="row-span-3 block h-full overflow-hidden rounded-l-lg"><TeamPanel abbr={c.team} playerId={c.player_id} name={c.name} muted={idle} /></PlayerTrigger>
      <CardHeader className="flex flex-row items-start justify-between gap-2 pt-2.5 sm:pt-3">
        <div className="min-w-0">
          <CardTitle className={`line-clamp-2 text-balance font-display text-[1.65rem] font-semibold leading-none tracking-tight ${idle ? "text-foreground/70" : ""}`}>
            <PlayerTrigger p={c} className={`rounded-sm ${NAME_LINK}`}>{c.name}</PlayerTrigger>
          </CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            <span className="font-semibold text-foreground/80">{c.pos}</span>
            <span aria-hidden> · </span>
            {t.name || c.team}
          </p>
        </div>
        <div className="flex max-w-[11.5rem] shrink-0 flex-wrap justify-end gap-1">
          {showCall && <Badge variant={CALL[c.call]} shape="chip">{c.call}</Badge>}
          {c.close_call && <Badge variant="close" shape="chip">close call</Badge>}
          {hurt && (
            <Badge variant={TAG[injuryTone(c.injury)]} shape="chip" title={c.injury_note ?? undefined}>
              {injuryLabel(c.injury.replaceAll("_", " "), c.return_week, week)}
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="pt-2">
        <Matchup c={c} />
        {c.stat_line && <StatLine text={c.stat_line} className="mt-1" />}
        <div className="mt-3"><Numbers c={c} /></div>
        <Reason text={c.reason} />
        <FormStrip form={c.form} className="mt-2" />
      </CardContent>
      <CardFooter className="flex flex-col items-stretch rounded-none border-t border-foreground/10 bg-transparent pt-2 pb-3 sm:pb-3.5">
        <Outlook c={c} runBusy={runBusy} />
      </CardFooter>
    </UiCard>
  );
}

export function RosterCards({ cards, week, runBusy }: { cards: Card[]; week: number; runBusy: boolean }) {
  return (
    <div className="space-y-7">
      {GROUPS.map(({ key, title }) => {
        const rows = cards.filter((c) => c.group === key);
        if (rows.length === 0) return null;
        return (
          <section key={key} className="space-y-3" aria-labelledby={`group-${key}`}>
            <h2 id={`group-${key}`} className="flex items-baseline gap-3">
              <span className="font-display text-xl font-semibold uppercase tracking-[0.14em]">{title}</span>
              <span className="font-display text-sm font-medium tabular-nums text-muted-foreground">{rows.length}</span>
              <Separator className="mb-1.5 flex-1 self-end bg-foreground/12" />
            </h2>
            <div className="grid gap-3 md:grid-cols-2">
              {rows.map((c) => <PlayerCard key={c.player_id} c={c} week={week} runBusy={runBusy} />)}
            </div>
          </section>
        );
      })}
    </div>
  );
}
