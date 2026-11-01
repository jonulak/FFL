import { AccountSettings, DeleteAccount } from "@/components/account-settings";
import { ErrorCard } from "@/components/error-card";
import { EspnConnection } from "@/components/espn-connection";
import { LlmSettings } from "@/components/llm-settings";
import { NotifySettings } from "@/components/notify-settings";
import { SignOutButton } from "@/components/sign-out-button";
import { UsersAdmin } from "@/components/users-admin";
import { apiGet, type Account, type AdminUser, type LlmToken, type LlmUsage, type NotifyConfig } from "@/lib/api";
import { errorFields } from "@/lib/errors";
import type { EspnStatus, ExtensionDevice } from "@/lib/espn";

export const dynamic = "force-dynamic";

// What is every league's: this device's notifications, the quiet hours, the ESPN logins and the leagues followed
// through them (#134), the account (its password, the devices signed in, deleting it) and, for an admin, the users. A
// league's own settings (its name, its switches, decisions and caps) are /settings/league, from the gear beside it in
// the league switcher.
export default async function Page() {
  let config: NotifyConfig, account: Account, espn: EspnStatus, devices: { devices: ExtensionDevice[] }, usage: LlmUsage | null;
  let users: AdminUser[] | null, token: LlmToken | null;
  try {
    [config, account, espn, devices, usage] = await Promise.all([
      apiGet<NotifyConfig>("/notify/config"), apiGet<Account>("/account"),
      apiGet<EspnStatus>("/espn/status"), apiGet<{ devices: ExtensionDevice[] }>("/espn/devices"),
      apiGet<LlmUsage>("/llm/usage?days=7").catch(() => null),  // admins only (403 for anyone else, no line); never worth failing Settings over
    ]);
    [users, token] = account.is_admin
      ? await Promise.all([apiGet<{ users: AdminUser[] }>("/admin/users").then((r) => r.users), apiGet<LlmToken>("/llm/token")])
      : [null, null];
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return (
    <div className="space-y-5">
      <h1 className="font-display text-4xl font-bold leading-none tracking-tight">Settings</h1>
      <NotifySettings config={config} />
      <EspnConnection initial={espn} devices={devices.devices} />
      <AccountSettings account={account} />
      {users && <UsersAdmin initial={users} me={account.username} />}
      {token && <LlmSettings initial={token} />}
      {usage && <p className="border-t border-foreground/10 pt-5 text-sm text-muted-foreground tabular-nums">
        This week: outlooks {cost(usage, "outlook")}, news {cost(usage, "news")}
      </p>}
      <div className="border-t border-foreground/10 pt-5"><SignOutButton /></div>
      <div className="border-t border-foreground/10 pt-5"><DeleteAccount username={account.username ?? ""} /></div>
    </div>
  );
}

// a kind prefix: "news" sums the write-ups (news) and the tagging (news_tag)
function cost(usage: LlmUsage, kind: string): string {
  const usd = usage.rows.filter((r) => r.kind.startsWith(kind)).reduce((sum, r) => sum + r.cost_usd, 0);
  return `$${usd.toFixed(2)}`;
}
