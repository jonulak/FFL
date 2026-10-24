import { ErrorCard } from "@/components/error-card";
import { FaToggle } from "@/components/fa-toggle";
import { RefreshOnMoves } from "@/components/refresh-on-moves";
import { StaleBadge } from "@/components/stale-badge";
import { WaiverSearch } from "@/components/waiver-search";
import { WaiverTable } from "@/components/waiver-table";
import { PlayerPosTabs, PlayerViewProvider } from "@/components/player-view-context";
import { PlayerViewBar } from "@/components/player-view-bar";
import { ClaimsProvider, type TabPlayer } from "@/components/claims-context";
import { ClaimsPanel } from "@/components/claims-panel";
import { WatchList } from "@/components/watch-list";
import { apiGet, type Claims, type Players, type WatchPage } from "@/lib/api";
import { errorFields } from "@/lib/errors";
import { isFreeAgent } from "@/lib/players";
import { columnSpec, sortFor } from "@/lib/player-columns";
import { FLEX, WATCH } from "@/lib/player-view";
import { Presence, Refade } from "@/components/motion";

export const dynamic = "force-dynamic";

type Search = { pos?: string; q?: string; fa?: string; preset?: string; cols?: string; sort?: string; f?: string | string[]; watch?: string };
const KEYS = ["q", "pos", "fa", "preset", "cols", "sort"] as const;

// A saved view of every player in the league (ffl-8iz): `?preset=` names it and the rest of the URL
// is what has changed from it (lib/player-view.ts): the position (ALL for every one), the Free agents
// toggle (fa=0 off), the columns, the filters (f, repeated) and the sort. The API resolves them, so
// they pass through verbatim, and a malformed one is its 422. `?q=` searches every player at every
// position by name or team, whatever the view says, and outranks the position, which stays in the
// URL only so clearing the search returns to that tab. `?watch=1` (the Watch tab) puts the watch list
// (GET /watch) in the table's place; the view is still read for the tabs and the way back.
export default async function Page({ searchParams }: { searchParams: Promise<Search> }) {
  const sp = await searchParams;
  const watching = sp.watch === "1";
  const q = watching ? "" : (sp.q ?? "").trim();
  const qs = new URLSearchParams();
  for (const k of KEYS) if (sp[k] !== undefined && !(watching && k === "q")) qs.set(k, sp[k]);
  for (const f of [sp.f ?? []].flat()) qs.append("f", f);
  // the claim plan loads beside the pool; its failure leaves the table without the panel, never the page
  const [pool, plan, watchPage] = await Promise.allSettled([
    apiGet<Players>(`/players?${qs}`),
    apiGet<Claims>("/claims"),
    watching ? apiGet<WatchPage>("/watch") : Promise.resolve(null),
  ]);
  if (pool.status === "rejected") return <ErrorCard {...errorFields(pool.reason)} />;
  if (watchPage.status === "rejected") return <ErrorCard {...errorFields(watchPage.reason)} />;
  const watch = watchPage.value;
  const data = pool.value;
  const pos = data.pos ?? data.view.pos;
  const sortLabel = columnSpec(sortFor(data.view.cols, data.view.sort).id)?.label ?? "name";
  const claims = plan.status === "fulfilled" ? plan.value : null;
  const claimsFault = plan.status === "rejected" ? errorFields(plan.reason) : null;
  // the claims panel names free agents only: a rostered player is never a claim target
  const shown = watch ? [...watch.groups.moving, ...watch.groups.quiet, ...watch.groups.elsewhere] : data.rows;
  const tab: TabPlayer[] = shown.filter(isFreeAgent).map((r) => ({
    player_id: r.player_id, name: r.name, pos: r.pos, team: r.team, add: r.add, ros_adj: r.ros_adj, status: r.status, waiver_date: r.waiver_date,
    opponent: r.opponent, home: r.home, kickoff: r.kickoff, bye: r.bye, game: r.game, injury: r.injury, injury_note: r.injury_note, return_week: r.return_week,
  }));
  const table = (
    <PlayerViewProvider applied={data.view} presets={data.presets} defaultId={data.default} q={q} capped={data.capped}>
      <div className="min-w-0 space-y-3">
        {/* phone: the field, then the Free agents toggle, over the tabs; from `sm` the toggle and the
            field close the tab strip's line, one hairline under all three */}
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-4 sm:border-b sm:border-foreground/12">
          {!watch && <div className="sm:py-1"><FaToggle /></div>}
          <div className="min-w-0 flex-1 sm:order-first"><PlayerPosTabs active={watch ? WATCH : q ? null : data.view.pos} /></div>
          <div className="order-first sm:order-none sm:py-1"><WaiverSearch q={q} /></div>
        </div>
        {watch ? (
          <>
            <p className="hidden max-w-[68ch] text-xs leading-4 text-muted-foreground md:block">
              Checked every hour: a hurt teammate ahead of him (Depth), a jump in snaps, targets or red-zone looks (Usage),
              good news (News), his worth to your roster turning positive (Bench). Moving: one lit in the last 3 days, or just dropped (Available).
            </p>
            <WatchList initial={watch} />
          </>
        ) : <>
        <PlayerViewBar />
        <Presence show={!!q && data.rows.length > 0} fold>
          <p className="font-display text-sm font-semibold uppercase tracking-[0.12em] text-muted-foreground" aria-live="polite">
            {data.rows.length} matching <span className="normal-case tracking-normal text-foreground">&ldquo;{q}&rdquo;</span> · every player
          </p>
        </Presence>
        {/* ALL or FLEX with no filter: the API cuts the pool to its first 150 by the view's sort */}
        <Presence show={data.capped && !q} fold>
          <p className="font-display text-sm font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            {data.view.pos === FLEX ? "RB, WR and TE" : "Every position"} · the first 150 by <span className="text-foreground">{sortLabel}</span> · add a filter to reach the rest
          </p>
        </Presence>
        {/* another position, preset, filter or column set, the Free agents toggle or a search: the new pool fades in, in place */}
        <Refade k={`${data.view.pos}|${data.fa}|${q}|${data.view.cols.join()}|${JSON.stringify(data.view.filters)}`}>
          <WaiverTable rows={data.rows} roster={data.roster ?? []} week={data.week} pos={pos} q={q || null} fa={data.fa} />
        </Refade>
        </>}
      </div>
    </PlayerViewProvider>
  );
  return (
    <div className={`space-y-5 ${claims ? "max-[112rem]:pb-28" : ""}`}>
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
      <p className={`hidden max-w-[72ch] text-xs leading-relaxed text-muted-foreground ${watch ? "" : "md:block"}`}>
        add = projected points gained this season by adding him (STRATEGY.md, Waivers; on a rostered player, what he&apos;d be worth to me, blank on my own).
        ROS = the models&apos; per-game rate shrunk toward his last three games by their snap
        share. beat and pace = his last three games against the projection, per game and over the
        rest of the season; opp and snap = his usage. START marks a player who would go straight into
        this week&apos;s lineup, with the gain; AHEAD lists the hurt teammates at his position. Tap a
        name for the full form line and the game log. View picks the columns and filters; a preset saves them.
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
      {/* the table takes every other page's 64rem column, lined up with the nav. The week's claim groups stand
          outside it, to the right, once the margin holds the 22rem dock (112rem viewport); below that they are a
          bottom bar and sheet, so the table never gives up width */}
      {claims ? (
        <ClaimsProvider initial={claims} players={tab}>
          <div data-claims-dock className="min-[112rem]:relative">
            {table}
            <div className="min-[112rem]:absolute min-[112rem]:inset-y-0 min-[112rem]:left-[calc(100%+1.5rem)] min-[112rem]:w-[22rem]">
              <ClaimsPanel />
            </div>
          </div>
        </ClaimsProvider>
      ) : table}
    </div>
  );
}
