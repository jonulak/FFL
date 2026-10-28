import { AccountSettings, DeleteAccount } from "@/components/account-settings";
import { SignOutButton } from "@/components/sign-out-button";
import { Toaster } from "@/components/ui/sonner";
import { apiGet, type Account, type LeagueEntry } from "@/lib/api";
import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

// A signed-in user with no league (ffl-soc.6.2.2: the API answers 404 no_leagues and lib/api.ts sends them here).
// Outside the app like the sign-in page (app/layout.tsx renders it bare). Adding a league is 6.3's; until then this
// says so, and holds the account (ffl-soc.6.2.4: /settings reads a league, so it is out of their reach). A user who
// has a league by now goes back to the start page.
export default async function Page() {
  const [leagues, account] = await Promise.all([
    apiGet<LeagueEntry[]>("/leagues").catch((): LeagueEntry[] => []),
    apiGet<Account>("/account").catch(() => null),
  ]);
  if (leagues.length) redirect("/");
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-sm flex-col justify-center gap-8 px-4 pt-12 pb-[max(1.75rem,calc(env(safe-area-inset-bottom)+0.75rem))]">
      <div className="space-y-2">
        <h1 className="font-display text-[2.6rem] font-bold leading-none tracking-tight">FFL</h1>
        <p className="text-sm text-muted-foreground">
          You&apos;re signed in, but no league is linked to your account yet. Your leagues will show here once
          they&apos;re added.
        </p>
      </div>
      {account && <AccountSettings account={account} />}
      <SignOutButton />
      {account && <div className="border-t border-foreground/10 pt-5"><DeleteAccount email={account.email} /></div>}
      <Toaster position="bottom-center" />
    </main>
  );
}
