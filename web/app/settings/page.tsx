import { CapsSettings } from "@/components/caps-settings";
import { DecisionSettings } from "@/components/decision-prefs";
import { ErrorCard } from "@/components/error-card";
import { NotifySettings } from "@/components/notify-settings";
import { apiGet, type NotifyConfig } from "@/lib/api";
import type { Caps } from "@/lib/caps";
import { errorFields } from "@/lib/errors";

export const dynamic = "force-dynamic";

// The device and quiet hours are every league's; the switches, the decisions and the caps are the open league's.
export default async function Page() {
  let config: NotifyConfig, caps: Caps;
  try {
    [config, caps] = await Promise.all([apiGet<NotifyConfig>("/notify/config"), apiGet<Caps>("/caps")]);
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return (
    <div className="space-y-5">
      <h1 className="font-display text-4xl font-bold leading-none tracking-tight">Settings</h1>
      <NotifySettings config={config} league={caps.name} />
      <DecisionSettings />
      <CapsSettings caps={caps} />
    </div>
  );
}
