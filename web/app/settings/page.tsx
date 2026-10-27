import { ErrorCard } from "@/components/error-card";
import { NotifySettings } from "@/components/notify-settings";
import { SignOutButton } from "@/components/sign-out-button";
import { apiGet, type NotifyConfig } from "@/lib/api";
import { errorFields } from "@/lib/errors";

export const dynamic = "force-dynamic";

// What is every league's: this device's notifications and the quiet hours. A league's own settings (its name, its
// switches, decisions and caps) are /settings/league, from the gear beside it in the league switcher.
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
      <div className="border-t border-foreground/10 pt-5"><SignOutButton /></div>
    </div>
  );
}
