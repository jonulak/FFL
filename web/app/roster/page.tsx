import { ErrorCard } from "@/components/error-card";
import { OutlookButton } from "@/components/outlook-button";
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
      </div>
      {/* The definition is a laptop read; on the phone the first screen belongs to the cards. */}
      <p className="max-w-[72ch] text-xs leading-relaxed text-muted-foreground">
        <span className="hidden md:inline">
          Call = optimal lineup by projection (STRATEGY.md, Lineup); close call under 2 points or a
          source spread of 3+. Outlooks are bullets written by Claude from the card&apos;s facts and recent news; each links its sources.
        </span>
        {/* A run that died leaves the old counts standing, so report the failure instead of them. */}
        {run.error
          ? ` Last run failed: ${run.error}.`
          : run.at
            ? ` Last run ${run.at}: ${run.generated} written, ${run.skipped} unchanged, ${run.failed} failed.` +
              // a per-card CLI failure does not kill the run, so its reason only shows up here
              (run.failed > 0 && run.last_failure ? ` First failure: ${run.last_failure}.` : "")
            : ""}
      </p>
      <RosterLive initial={data} />
    </div>
  );
}
