import { DecisionSettings } from "@/components/decision-prefs";
import { ErrorCard } from "@/components/error-card";
import { NotifySettings } from "@/components/notify-settings";
import { apiGet, type NotifyConfig } from "@/lib/api";
import { errorFields } from "@/lib/errors";

export const dynamic = "force-dynamic";

export default async function Page() {
  let config: NotifyConfig;
  try {
    config = await apiGet<NotifyConfig>("/notify/config");
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return (
    <div className="space-y-5">
      <h1 className="font-display text-4xl font-bold leading-none tracking-tight">Settings</h1>
      <NotifySettings config={config} />
      <DecisionSettings />
    </div>
  );
}
