import { CapsSettings } from "@/components/caps-settings";
import { DecisionSettings } from "@/components/decision-prefs";
import { ErrorCard } from "@/components/error-card";
import { LeagueName } from "@/components/league-name";
import { NotifySwitches } from "@/components/notify-settings";
import { apiGet, type NotifyConfig } from "@/lib/api";
import type { Caps } from "@/lib/caps";
import { errorFields } from "@/lib/errors";

export const dynamic = "force-dynamic";

// One league's settings, opened from the gear beside it in the league switcher (?league= names it; none is home's):
// its name, which of its events push, the decision log and its roster caps. The device and quiet hours are /settings.
export default async function Page() {
  let config: NotifyConfig, caps: Caps;
  try {
    [config, caps] = await Promise.all([apiGet<NotifyConfig>("/notify/config"), apiGet<Caps>("/caps")]);
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return (
    <div className="space-y-5">
      <div className="space-y-1.5">
        <h1 className="font-display text-4xl font-bold leading-none tracking-tight">League settings</h1>
        <p className="truncate text-sm text-muted-foreground">{caps.name}</p>
      </div>
      <LeagueName name={caps.name} />
      <NotifySwitches prefs={config.prefs} />
      <DecisionSettings />
      <CapsSettings caps={caps} />
    </div>
  );
}
