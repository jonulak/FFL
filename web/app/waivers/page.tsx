import { ErrorCard } from "@/components/error-card";
import { PosTabs } from "@/components/pos-tabs";
import { StaleBadge } from "@/components/stale-badge";
import { WaiverTable } from "@/components/waiver-table";
import { apiGet, type Waivers } from "@/lib/api";
import { errorFields } from "@/lib/errors";

export const dynamic = "force-dynamic";

export default async function Page({ searchParams }: { searchParams: Promise<{ pos?: string }> }) {
  const { pos = "RB" } = await searchParams;
  let data: Waivers;
  try {
    data = await apiGet<Waivers>(`/waivers?pos=${encodeURIComponent(pos)}`);
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
        <StaleBadge since={data.stale_since} />
      </div>
      {/* The definition is a laptop read; on the phone the first screen belongs to the table. */}
      <p className="hidden max-w-[72ch] text-xs leading-relaxed text-muted-foreground md:block">
        add = projected points gained this season by adding him (STRATEGY.md, Waivers), ranked
        top down. ROS = Sleeper&apos;s remaining weeks and CBS&apos;s rest-of-season projection, averaged;
        ± is the gap between them. Δ roster = ROS vs my weakest at the position; Δ pool = ROS vs league
        replacement. START marks a player who would go straight into this week&apos;s lineup, with the gain.
        Last 3 games = what he has actually done, read by hand and never in add; each column&apos;s
        heading says what it is.
      </p>
      {data.ros_source === "season" && (
        <p className="max-w-[72ch] text-xs leading-relaxed text-close-foreground dark:text-close">
          ROS from season totals: neither Sleeper&apos;s remaining weeks nor CBS&apos;s rest-of-season page
          answered, so every ROS here is a draft-day season projection scaled by the weeks left.
        </p>
      )}
      <div className="space-y-3">
        <PosTabs active={pos} base="/waivers" />
        <WaiverTable rows={data.rows} roster={data.roster ?? []} week={data.week} pos={pos} />
      </div>
    </div>
  );
}
