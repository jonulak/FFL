import { ErrorCard } from "@/components/error-card";
import { OutlookButton } from "@/components/outlook-button";
import { PositionCaps } from "@/components/position-caps";
import { RosterLive } from "@/components/roster-live";
import { StaleBadge } from "@/components/stale-badge";
import { apiGet, type Roster } from "@/lib/api";
import { errorFields } from "@/lib/errors";

export const dynamic = "force-dynamic";

export default async function Page() {
  let data: Roster;
  try {
    data = await apiGet<Roster>("/roster");
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  const run = data.outlook_run;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
        <h1 className="font-display text-4xl font-bold leading-none tracking-tight">
          Roster
          <span className="ml-2.5 font-semibold text-muted-foreground">Week {data.week}</span>
        </h1>
        <StaleBadge since={data.stale_since} />
        <div className="ml-auto"><OutlookButton running={run.running} /></div>
        <PositionCaps caps={data.position_caps} className="basis-full" />
      </div>
      <RosterLive initial={data} />
    </div>
  );
}
