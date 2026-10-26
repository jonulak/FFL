import type { Metadata } from "next";
import { Barlow_Condensed, Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { DecisionPrefsProvider } from "@/components/decision-prefs";
import { LeagueProvider } from "@/components/league-context";
import { Nav } from "@/components/nav";
import { PageTransition } from "@/components/page-transition";
import { PlayerDialogProvider } from "@/components/player-dialog";
import { SwRegister } from "@/components/sw-register";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { WatchProvider } from "@/components/watch-context";
import { headers } from "next/headers";
import { apiGet, type DecisionPrefs, type LeagueEntry, type WatchIds } from "@/lib/api";
import { LEAGUE_ARRIVE, LEAGUE_HEADER } from "@/lib/league";
import { DEFAULT_DECISION_PREFS } from "@/lib/decisions";

const geistSans = Geist({ variable: "--font-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
// The board face: condensed, for names and numerals, the way a broadcast lineup graphic sets them.
const barlow = Barlow_Condensed({ variable: "--font-barlow", subsets: ["latin"], weight: ["500", "600", "700"] });

export const metadata: Metadata = {
  title: "FFL", description: "2026 season cockpit",
  appleWebApp: { capable: true, title: "FFL", statusBarStyle: "default" },
};

// Before the first paint: a league switch's page (the switcher set LEAGUE_ARRIVE just before the load) is marked
// arriving for the length of its arrival (globals.css, "League switch"), then unmarked; any other load is untouched.
// The marker holds the width the old league's name stood at, which the switcher grows or shrinks the new one from.
const ARRIVE_SCRIPT = `try{var v=sessionStorage.getItem(${JSON.stringify(LEAGUE_ARRIVE)});if(v){sessionStorage.removeItem(${JSON.stringify(LEAGUE_ARRIVE)});var d=document.documentElement;d.dataset.leagueArriving="";d.dataset.leagueFromW=v;setTimeout(function(){delete d.dataset.leagueArriving},1500)}}catch(e){}`;

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // every page renders under this layout: an API that cannot answer leaves the switches at their defaults, never an error
  // and the watched players' ids, so a star draws filled on the first paint; unread, every star starts empty
  // the leagues for the switcher, and the one this document's URL names (web/proxy.ts: its header); every call here
  // reads that league, and a switch is a full load, so the layout never shows another league's stars or switches
  const [prefs, watched, all, open] = await Promise.all([
    apiGet<DecisionPrefs>("/decisions/prefs").catch(() => DEFAULT_DECISION_PREFS),
    apiGet<WatchIds>("/watch/ids").catch((): WatchIds => ({ ids: [] })),
    apiGet<LeagueEntry[]>("/leagues").catch((): LeagueEntry[] => []),
    headers().then((h) => h.get(LEAGUE_HEADER)),
  ]);
  const league = all.find((l) => (open ? l.key === open : l.home)) ?? null;
  // The font variables belong on <html>: globals.css applies `font-sans` there, and
  // `--font-sans` has to be defined on the same element or above it.
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} ${barlow.variable}`} suppressHydrationWarning>
      <head><script dangerouslySetInnerHTML={{ __html: ARRIVE_SCRIPT }} /></head>
      <body className="min-h-dvh bg-background text-foreground antialiased">
        <SwRegister />
        {/* the season modal: one dialog for the app, opened from any player row */}
        <TooltipProvider delay={400}>
          <DecisionPrefsProvider prefs={prefs}>
            <WatchProvider ids={watched.ids}>
              <LeagueProvider league={league && !league.home ? league.key : null}>
              <PlayerDialogProvider>
                <Nav leagues={all} league={league} />
                {/* one 64rem column on every page, the nav's width; the Players page's claims dock stands outside it */}
                <main className="mx-auto max-w-5xl px-3 py-4"><PageTransition>{children}</PageTransition></main>
              </PlayerDialogProvider>
              </LeagueProvider>
            </WatchProvider>
          </DecisionPrefsProvider>
        </TooltipProvider>
        <Toaster position="bottom-center" />
      </body>
    </html>
  );
}
