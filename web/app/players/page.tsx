import { ErrorCard } from "@/components/error-card";
import { FaToggle } from "@/components/fa-toggle";
import { PosTabs } from "@/components/pos-tabs";
import { RefreshOnMoves } from "@/components/refresh-on-moves";
import { StaleBadge } from "@/components/stale-badge";
import { WaiverSearch } from "@/components/waiver-search";
import { WaiverTable } from "@/components/waiver-table";
import { ClaimsProvider, type TabPlayer } from "@/components/claims-context";
import { ClaimsPanel } from "@/components/claims-panel";
import { apiGet, type Claims, type Players } from "@/lib/api";
import { errorFields } from "@/lib/errors";
import { isFreeAgent } from "@/lib/players";
import { Presence } from "@/components/motion";
import { ViewTransition } from "react";

export const dynamic = "force-dynamic";

// Every player in the league at a position, or only the free agents (`?fa=0` turns the Free agents
// toggle off; absent, it is on). `?q=` searches every player at every position by name or team,
// whatever the toggle says, and outranks `?pos=`, which stays in the URL only so clearing the
// search returns to that tab.
export default async function Page({ searchParams }: { searchParams: Promise<{ pos?: string; q?: string; fa?: string }> }) {
  const { pos = "RB", q: raw = "", fa: faParam } = await searchParams;
  const q = raw.trim();
  const fa = faParam !== "0";
  // the claim plan loads beside the pool; its failure leaves the table without the panel, never the page
  const [pool, plan] = await Promise.allSettled([
    apiGet<Players>(`/players?pos=${encodeURIComponent(pos)}&fa=${fa ? 1 : 0}${q ? `&q=${encodeURIComponent(q)}` : ""}`),
    apiGet<Claims>("/claims"),
  ]);
  if (pool.status === "rejected") return <ErrorCard {...errorFields(pool.reason)} />;
  const data = pool.value;
  const claims = plan.status === "fulfilled" ? plan.value : null;
  const claimsFault = plan.status === "rejected" ? errorFields(plan.reason) : null;
  // the claims panel names free agents only: a rostered player is never a claim target
  const tab: TabPlayer[] = data.rows.filter(isFreeAgent).map((r) => ({
    player_id: r.player_id, name: r.name, pos: r.pos, team: r.team, add: r.add, ros_adj: r.ros_adj, status: r.status, waiver_date: r.waiver_date,
  }));
  const table = (
    <div className="min-w-0 space-y-3">
      {/* phone: the field, then the Free agents toggle, over the tabs; from `sm` the toggle and the
          field close the tab strip's line, one hairline under all three */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-4 sm:border-b sm:border-foreground/12">
        <div className="sm:py-1"><FaToggle fa={fa} pos={pos} q={q} /></div>
        <div className="min-w-0 flex-1 sm:order-first"><PosTabs active={q ? null : pos} base="/players" extra={fa ? "" : "&fa=0"} /></div>
        <div className="order-first sm:order-none sm:py-1"><WaiverSearch q={q} pos={pos} fa={fa} /></div>
      </div>
      <Presence show={!!q && data.rows.length > 0} fold>
        <p className="font-display text-sm font-semibold uppercase tracking-[0.12em] text-muted-foreground" aria-live="polite">
          {data.rows.length} matching <span className="normal-case tracking-normal text-foreground">&ldquo;{q}&rdquo;</span> · every player, by add
        </p>
      </Presence>
      {/* another position, the Free agents toggle or a search: the new pool crossfades in over the old, in place */}
      <ViewTransition update="swap" default="none">
        <div><WaiverTable rows={data.rows} roster={data.roster ?? []} week={data.week} pos={pos} q={q || null} fa={fa} /></div>
      </ViewTransition>
    </div>
  );
  return (
    <div className={`space-y-5 ${claims ? "max-lg:pb-28" : ""}`}>
      <RefreshOnMoves />
      <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
        <h1 className="font-display text-4xl font-bold leading-none tracking-tight">
          Players
          <span className="ml-2.5 font-semibold text-muted-foreground">Week {data.week}</span>
        </h1>
        {/* the week's games are all played: ROS starts from the next one, and nothing is left to gain this week */}
        <Presence show={data.week_over}>
          <span className="font-display text-sm font-semibold uppercase tracking-[0.12em] text-muted-foreground" title="every game this week has kicked off and finished; ESPN rolls its week later, ROS does not wait">
            games played · ROS from week {data.ros_week}
          </span>
        </Presence>
        <StaleBadge since={data.stale_since} />
      </div>
      {/* The definition is a laptop read; on the phone the first screen belongs to the table. */}
      <p className="hidden max-w-[72ch] text-xs leading-relaxed text-muted-foreground md:block">
        add = projected points gained this season by adding him (STRATEGY.md, Waivers; on a rostered player, what he&apos;d be worth to me, blank on my own), ranked
        top down. ROS = the models&apos; per-game rate shrunk toward his last three games by their snap
        share. beat and pace = his last three games against the projection, per game and over the
        rest of the season; opp and snap = his usage. START marks a player who would go straight into
        this week&apos;s lineup, with the gain; AHEAD lists the hurt teammates at his position. Tap a
        name for the rest: the models&apos; ROS and spread, Δ roster and Δ pool, the full form line
        and the game log.
      </p>
      <Presence show={data.ros_source === "season"} fold>
        <p className="max-w-[72ch] text-xs leading-relaxed text-close-foreground dark:text-close">
          ROS from season totals: none of the rest-of-season sources
          answered, so every ROS here is a draft-day season projection scaled by the weeks left.
        </p>
      </Presence>
      {claimsFault && (
        <p className="max-w-[72ch] text-xs leading-relaxed text-destructive">
          The claims plan didn&apos;t load ({claimsFault.source} · {claimsFault.message}), so the claim tools are off. The pool below is current.
        </p>
      )}
      {/* the table, with the week's claim groups docked to its right from `lg` (a bottom sheet below). The page
          keeps every other page's 64rem column, so the table lines up with the nav: once the margin can hold the
          22rem dock (112rem viewport) it stands outside the column and the table takes the column's full width */}
      {claims ? (
        <ClaimsProvider initial={claims} players={tab}>
          <div data-claims-dock className="lg:grid lg:grid-cols-[minmax(0,1fr)_22rem] lg:items-start lg:gap-6 min-[112rem]:relative min-[112rem]:block">
            {table}
            <div className="lg:self-stretch min-[112rem]:absolute min-[112rem]:inset-y-0 min-[112rem]:left-[calc(100%+1.5rem)] min-[112rem]:w-[22rem]">
              <ClaimsPanel />
            </div>
          </div>
        </ClaimsProvider>
      ) : table}
    </div>
  );
}
