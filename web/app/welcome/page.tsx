import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { AccountSettings, DeleteAccount } from "@/components/account-settings";
import { ConnectWizard, type InstallLinks } from "@/components/connect-wizard";
import { SignOutButton } from "@/components/sign-out-button";
import { Toaster } from "@/components/ui/sonner";
import { apiGet, type Account, type LeagueEntry, type LlmToken } from "@/lib/api";
import { platformOf, type EspnStatus } from "@/lib/espn";

export const dynamic = "force-dynamic";

const NO_STATUS: EspnStatus = { connected: false, failing_since: null, accounts: [], devices: 0 };

// The connect wizard (ffl-soc.6.3.5): a signed-in user with no league lands here (ffl-soc.6.2.2: the API answers 404
// no_leagues and lib/api.ts sends them here), and Settings › Add an ESPN login opens it with ?add=1 (?paste=1: straight
// to the paste form). Outside the app like the sign-in page (app/layout.tsx renders it bare). A first run also holds
// the account (ffl-soc.6.2.4: /settings reads a league, so it is out of their reach); a user who has a league by now
// and didn't ask to add one goes back to the start page.
export default async function Page({ searchParams }: { searchParams: Promise<{ add?: string; paste?: string; from?: string }> }) {
  const { add, paste, from } = await searchParams;
  const [leagues, account, status, h] = await Promise.all([
    apiGet<LeagueEntry[]>("/leagues").catch((): LeagueEntry[] => []),
    apiGet<Account>("/account").catch(() => null),
    apiGet<EspnStatus>("/espn/status").catch(() => NO_STATUS),
    headers(),
  ]);
  // the Safari app's Open FFL button opens ?from=extension: connecting this browser, a first run or another login for a user who
  // has leagues already
  const adding = add === "1" || (from === "extension" && leagues.length > 0);
  if (leagues.length && !adding) redirect("/");
  // an admin is asked for the server's Claude token first while it has none (a first run only; Settings › LLM after);
  // a failed read asks nothing
  const claude = account?.is_admin && !adding ? await apiGet<LlmToken>("/llm/token").catch(() => null) : null;
  // the store listings, once they exist (#136): none, and the step names the install with no button
  const links: InstallLinks = {
    testflight: process.env.FFL_TESTFLIGHT_URL || null,
    chrome: process.env.FFL_CHROME_STORE_URL || null,
    mac: process.env.FFL_MAC_APP_URL || null,
  };
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-md flex-col md:max-w-[55rem] gap-6 px-4 pt-3 pb-[max(1.75rem,calc(env(safe-area-inset-bottom)+0.75rem))]">
      <header className="flex h-11 items-center justify-between">
        <span className="font-display text-[1.6rem] font-bold leading-none tracking-tight">FFL</span>
        {adding
          ? <a href="/settings" className="text-sm text-muted-foreground hover:text-foreground">Back to Settings</a>
          : account && <a href="#account" className="text-sm text-muted-foreground hover:text-foreground">Account</a>}
      </header>
      <ConnectWizard platform={platformOf(h.get("user-agent") ?? "")} links={links} initial={status} add={adding}
                     hasLeagues={leagues.length > 0} startPaste={paste === "1"} claude={claude} />
      {!adding && account && (
        <section id="account" className="mt-10 space-y-5 border-t border-foreground/10 pt-6">
          <AccountSettings account={account} />
          <SignOutButton />
          <div className="border-t border-foreground/10 pt-5"><DeleteAccount username={account.username ?? ""} /></div>
        </section>
      )}
      <Toaster position="bottom-center" />
    </main>
  );
}
