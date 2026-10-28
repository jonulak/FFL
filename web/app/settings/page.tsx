import { AccountSettings, DeleteAccount } from "@/components/account-settings";
import { ErrorCard } from "@/components/error-card";
import { NotifySettings } from "@/components/notify-settings";
import { SignOutButton } from "@/components/sign-out-button";
import { apiGet, type Account, type NotifyConfig } from "@/lib/api";
import { errorFields } from "@/lib/errors";

export const dynamic = "force-dynamic";

// What is every league's: this device's notifications, the quiet hours, and the account (its sign-ins, the devices
// signed in, deleting it). A league's own settings (its name, its switches, decisions and caps) are /settings/league,
// from the gear beside it in the league switcher. `?link=` is a provider link's answer (ffl/auth.py).
export default async function Page({ searchParams }: { searchParams: Promise<{ link?: string }> }) {
  const { link } = await searchParams;
  let config: NotifyConfig, account: Account;
  try {
    [config, account] = await Promise.all([apiGet<NotifyConfig>("/notify/config"), apiGet<Account>("/account")]);
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return (
    <div className="space-y-5">
      <h1 className="font-display text-4xl font-bold leading-none tracking-tight">Settings</h1>
      <NotifySettings config={config} />
      <AccountSettings account={account} link={link} />
      <div className="border-t border-foreground/10 pt-5"><SignOutButton /></div>
      <div className="border-t border-foreground/10 pt-5"><DeleteAccount email={account.email} /></div>
    </div>
  );
}
