import { AccountSettings, DeleteAccount } from "@/components/account-settings";
import { AppSettingsCards } from "@/components/app-settings";
import { ErrorCard } from "@/components/error-card";
import { EspnConnection } from "@/components/espn-connection";
import { LlmSettings } from "@/components/llm-settings";
import { NotifySettings } from "@/components/notify-settings";
import { SignOutButton } from "@/components/sign-out-button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { UsersAdmin } from "@/components/users-admin";
import { apiGet, type Account, type AdminUser, type AppSettings, type LlmToken, type LlmUsage, type NotifyConfig } from "@/lib/api";
import { errorFields } from "@/lib/errors";
import type { EspnStatus, ExtensionDevice } from "@/lib/espn";

export const dynamic = "force-dynamic";

const TABS = [["you", "You"], ["app", "App"]] as const;

// What is every league's: this device's notifications, the quiet hours, the ESPN logins and the leagues followed
// through them (#134), the account (its password, the devices signed in, deleting it). A league's own settings (its
// name, its switches, decisions and caps) are /settings/league, from the gear beside it in the league switcher.
// An admin also gets an App tab: the install's own settings (kept in the database), Claude and its cost, the users.
export default async function Page() {
  let config: NotifyConfig, account: Account, espn: EspnStatus, devices: { devices: ExtensionDevice[] }, usage: LlmUsage | null;
  let admin: { users: AdminUser[]; token: LlmToken; app: AppSettings } | null;
  try {
    [config, account, espn, devices, usage] = await Promise.all([
      apiGet<NotifyConfig>("/notify/config"), apiGet<Account>("/account"),
      apiGet<EspnStatus>("/espn/status"), apiGet<{ devices: ExtensionDevice[] }>("/espn/devices"),
      apiGet<LlmUsage>("/llm/usage?days=7").catch(() => null),  // admins only (403 for anyone else, no line); never worth failing Settings over
    ]);
    admin = account.is_admin
      ? await Promise.all([apiGet<{ users: AdminUser[] }>("/admin/users"), apiGet<LlmToken>("/llm/token"),
                           apiGet<AppSettings>("/app/settings")])
          .then(([u, token, app]) => ({ users: u.users, token, app }))
      : null;
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  const you = (
    <>
      <NotifySettings config={config} />
      <EspnConnection initial={espn} devices={devices.devices} />
      <AccountSettings account={account} />
      <div className="border-t border-foreground/10 pt-5"><SignOutButton /></div>
      <div className="border-t border-foreground/10 pt-5"><DeleteAccount username={account.username ?? ""} /></div>
    </>
  );
  return (
    <div className="space-y-5">
      <h1 className="font-display text-4xl font-bold leading-none tracking-tight">Settings</h1>
      {admin ? (
        <Tabs defaultValue="you" className="gap-5">
          <div className="border-b border-foreground/12">
            <TabsList variant="line" indicatorInset="0.625rem" aria-label="Whose settings" className="h-10 justify-start gap-0 p-0">
              {TABS.map(([value, label]) => (
                <TabsTrigger key={value} value={value}
                  className="h-full flex-none rounded-none px-2.5 font-display text-[0.9375rem] font-semibold uppercase tracking-[0.12em] text-foreground/55 hover:text-foreground/85 data-active:text-foreground dark:text-foreground/55 dark:hover:text-foreground/85 dark:data-active:text-foreground">
                  {label}
                </TabsTrigger>
              ))}
            </TabsList>
          </div>
          <TabsContent value="you" className="space-y-5">{you}</TabsContent>
          <TabsContent value="app" className="space-y-5">
            <p className="max-w-[60ch] leading-relaxed text-muted-foreground">
              Settings for this whole install and everyone on it. They&apos;re kept in the database, so upgrades leave
              them in place.
            </p>
            <AppSettingsCards initial={admin.app} />
            <LlmSettings initial={admin.token} />
            {usage && <p className="text-muted-foreground tabular-nums">
              This week: outlooks {cost(usage, "outlook")}, news {cost(usage, "news")}
            </p>}
            <UsersAdmin initial={admin.users} me={account.username} />
          </TabsContent>
        </Tabs>
      ) : you}
    </div>
  );
}

// a kind prefix: "news" sums the write-ups (news) and the tagging (news_tag)
function cost(usage: LlmUsage, kind: string): string {
  const usd = usage.rows.filter((r) => r.kind.startsWith(kind)).reduce((sum, r) => sum + r.cost_usd, 0);
  return `$${usd.toFixed(2)}`;
}
