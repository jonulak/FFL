import { ErrorCard } from "@/components/error-card";
import { OutlookButton } from "@/components/outlook-button";
import { RosterCards } from "@/components/roster-cards";
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
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-xl font-semibold">Roster · week {data.week}</h1>
        <StaleBadge since={data.stale_since} />
        <div className="ml-auto"><OutlookButton running={run.running} /></div>
      </div>
      <p className="text-xs text-muted-foreground">
        Call = optimal lineup by projection (STRATEGY.md, Lineup); close call under 2 points or a
        source spread of 3+. Outlooks are written by Claude from the card&apos;s facts only.
        {/* A run that died leaves the old counts standing, so report the failure instead of them. */}
        {run.error
          ? ` Last run failed: ${run.error}.`
          : run.at
            ? ` Last run ${run.at}: ${run.generated} written, ${run.skipped} unchanged, ${run.failed} failed.`
            : ""}
      </p>
      <RosterCards cards={data.cards} week={data.week} />
    </div>
  );
}
