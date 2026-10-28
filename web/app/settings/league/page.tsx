import { DecisionSettings } from "@/components/decision-prefs";
import { ErrorCard } from "@/components/error-card";
import { LeagueName } from "@/components/league-name";
import { NotifySwitches } from "@/components/notify-settings";
import { apiGet, type LeagueEntry, type NotifyConfig } from "@/lib/api";
import { errorFields } from "@/lib/errors";
import { LEAGUE_HEADER } from "@/lib/league";
import { headers } from "next/headers";

export const dynamic = "force-dynamic";

// One league's settings, opened from the gear beside it in the league switcher (?league= names it; none is the
// user's default): its name, which of its events push and the decision log. The device and quiet hours are /settings.
// No roster caps or strategy here: those are the draft tools', which the web app does not have (ffl-srn).
export default async function Page() {
  let config: NotifyConfig, all: LeagueEntry[];
  const open = (await headers()).get(LEAGUE_HEADER);
  try {
    [config, all] = await Promise.all([apiGet<NotifyConfig>("/notify/config"), apiGet<LeagueEntry[]>("/leagues")]);
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  // /notify/config already answered for the open league, so it is one of these (the layout picks it the same way)
  const name = all.find((l) => (open ? l.key === open : l.home))?.name ?? "";
  return (
    <div className="space-y-5">
      <div className="space-y-1.5">
        <h1 className="font-display text-4xl font-bold leading-none tracking-tight">League settings</h1>
        <p className="truncate text-sm text-muted-foreground">{name}</p>
      </div>
      <LeagueName name={name} />
      <NotifySwitches prefs={config.prefs} />
      <DecisionSettings />
    </div>
  );
}
