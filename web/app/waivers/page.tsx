import { ErrorCard } from "@/components/error-card";
import { PosTabs } from "@/components/pos-tabs";
import { StaleBadge } from "@/components/stale-badge";
import { WaiverSearch } from "@/components/waiver-search";
import { WaiverTable } from "@/components/waiver-table";
import { apiGet, type Waivers } from "@/lib/api";
import { errorFields } from "@/lib/errors";

export const dynamic = "force-dynamic";

// `?q=` searches every position's pool by name or team and outranks `?pos=`, which stays in the
// URL only so clearing the search returns to that tab.
export default async function Page({ searchParams }: { searchParams: Promise<{ pos?: string; q?: string }> }) {
  const { pos = "RB", q: raw = "" } = await searchParams;
  const q = raw.trim();
  let data: Waivers;
  try {
    data = await apiGet<Waivers>(`/waivers?pos=${encodeURIComponent(pos)}${q ? `&q=${encodeURIComponent(q)}` : ""}`);
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
        <h1 className="font-display text-4xl font-bold leading-none tracking-tight">
          Waivers
          <span className="ml-2.5 font-semibold text-muted-foreground">Week {data.week}</span>
        </h1>
        {/* the week's games are all played: ROS starts from the next one, and nothing is left to gain this week */}
        {data.week_over && (
          <span className="font-display text-sm font-semibold uppercase tracking-[0.12em] text-muted-foreground" title="every game this week has kicked off and finished; ESPN rolls its week later, ROS does not wait">
            games played · ROS from week {data.ros_week}
          </span>
        )}
        <StaleBadge since={data.stale_since} />
      </div>
      {/* The definition is a laptop read; on the phone the first screen belongs to the table. */}
      <p className="hidden max-w-[72ch] text-xs leading-relaxed text-muted-foreground md:block">
        add = projected points gained this season by adding him (STRATEGY.md, Waivers), ranked
        top down. ROS = the models&apos; per-game rate shrunk toward his last three games, each game
        worth its snap share against the position&apos;s lead players (wt), the prior worth four games.
        model = Sleeper&apos;s remaining weeks and CBS&apos;s rest-of-season projection, averaged; ± is the
        gap between them. Δ roster = ROS vs my weakest at the position; Δ pool = ROS vs league
        replacement. START marks a player who would go straight into this week&apos;s lineup, with the gain.
        R marks a rookie; AHEAD lists the hurt teammates at his position, since a fill-in&apos;s window
        ends when they are back. Each column&apos;s heading says what it is.
      </p>
      {data.ros_source === "season" && (
        <p className="max-w-[72ch] text-xs leading-relaxed text-close-foreground dark:text-close">
          ROS from season totals: neither Sleeper&apos;s remaining weeks nor CBS&apos;s rest-of-season page
          answered, so every ROS here is a draft-day season projection scaled by the weeks left.
        </p>
      )}
      <div className="space-y-3">
        {/* phone: the field over the tabs, full width; from `sm` it closes the tab strip's line */}
        {/* phone: the field over the tabs, full width; from `sm` it closes the tab strip's line,
            one hairline under both */}
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-4 sm:border-b sm:border-foreground/12">
          <div className="min-w-0 flex-1 sm:order-first"><PosTabs active={q ? null : pos} base="/waivers" /></div>
          <div className="order-first sm:order-none sm:py-1"><WaiverSearch q={q} pos={pos} /></div>
        </div>
        {q && data.rows.length > 0 && (
          <p className="font-display text-sm font-semibold uppercase tracking-[0.12em] text-muted-foreground" aria-live="polite">
            {data.rows.length} matching <span className="normal-case tracking-normal text-foreground">&ldquo;{q}&rdquo;</span> · every position, by add
          </p>
        )}
        <WaiverTable rows={data.rows} roster={data.roster ?? []} week={data.week} pos={pos} q={q || null} />
      </div>
    </div>
  );
}
