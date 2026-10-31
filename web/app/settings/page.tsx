import { AccountSettings, DeleteAccount } from "@/components/account-settings";
import { ErrorCard } from "@/components/error-card";
import { EspnConnection } from "@/components/espn-connection";
import { NotifySettings } from "@/components/notify-settings";
import { SignOutButton } from "@/components/sign-out-button";
import { apiGet, type Account, type LlmUsage, type NotifyConfig } from "@/lib/api";
import { errorFields } from "@/lib/errors";
import type { EspnStatus, ExtensionDevice } from "@/lib/espn";

export const dynamic = "force-dynamic";

// What is every league's: this device's notifications, the quiet hours, the ESPN logins and the leagues followed
// through them (#134), and the account (its sign-ins, the devices signed in, deleting it). A league's own settings (its name, its switches, decisions and caps) are /settings/league,
// from the gear beside it in the league switcher. `?link=` is a provider link's answer (ffl/auth.py).
export default async function Page({ searchParams }: { searchParams: Promise<{ link?: string }> }) {
  const { link } = await searchParams;
  let config: NotifyConfig, account: Account, espn: EspnStatus, devices: { devices: ExtensionDevice[] }, usage: LlmUsage | null;
  try {
    [config, account, espn, devices, usage] = await Promise.all([
      apiGet<NotifyConfig>("/notify/config"), apiGet<Account>("/account"),
      apiGet<EspnStatus>("/espn/status"), apiGet<{ devices: ExtensionDevice[] }>("/espn/devices"),
      apiGet<LlmUsage>("/llm/usage?days=7").catch(() => null),  // operators only (403 for anyone else, no line); never worth failing Settings over
    ]);
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return (
    <div className="space-y-5">
      <h1 className="font-display text-4xl font-bold leading-none tracking-tight">Settings</h1>
      <NotifySettings config={config} />
      <EspnConnection initial={espn} devices={devices.devices} />
      <AccountSettings account={account} link={link} />
      {usage && <p className="border-t border-foreground/10 pt-5 text-sm text-muted-foreground tabular-nums">
        This week: outlooks {cost(usage, "outlook")}, news {cost(usage, "news")}
      </p>}
      <div className="border-t border-foreground/10 pt-5"><SignOutButton /></div>
      <div className="border-t border-foreground/10 pt-5"><DeleteAccount email={account.email} /></div>
    </div>
  );
}

function cost(usage: LlmUsage, kind: string): string {
  const usd = usage.rows.filter((r) => r.kind === kind).reduce((sum, r) => sum + r.cost_usd, 0);
  return `$${usd.toFixed(2)}`;
}
