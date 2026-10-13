import type { Metadata } from "next";
import { Barlow_Condensed, Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { DecisionPrefsProvider } from "@/components/decision-prefs";
import { Nav } from "@/components/nav";
import { PageTransition } from "@/components/page-transition";
import { PlayerDialogProvider } from "@/components/player-dialog";
import { SwRegister } from "@/components/sw-register";
import { Toaster } from "@/components/ui/sonner";
import { apiGet, DEFAULT_DECISION_PREFS, type DecisionPrefs } from "@/lib/api";

const geistSans = Geist({ variable: "--font-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
// The board face: condensed, for names and numerals, the way a broadcast lineup graphic sets them.
const barlow = Barlow_Condensed({ variable: "--font-barlow", subsets: ["latin"], weight: ["500", "600", "700"] });

export const metadata: Metadata = {
  title: "FFL", description: "2026 season cockpit",
  appleWebApp: { capable: true, title: "FFL", statusBarStyle: "default" },
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // every page renders under this layout: an API that cannot answer leaves the switches at their defaults, never an error
  const prefs = await apiGet<DecisionPrefs>("/decisions/prefs").catch(() => DEFAULT_DECISION_PREFS);
  // The font variables belong on <html>: globals.css applies `font-sans` there, and
  // `--font-sans` has to be defined on the same element or above it.
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} ${barlow.variable}`} suppressHydrationWarning>
      <body className="min-h-dvh bg-background text-foreground antialiased">
        <SwRegister />
        {/* the season modal: one dialog for the app, opened from any player row */}
        <DecisionPrefsProvider prefs={prefs}>
          <PlayerDialogProvider>
            <Nav />
            {/* the Players page with its claims dock gets 86rem from `xl`, so the table still fits beside it */}
            <main className="mx-auto max-w-5xl px-3 py-4 xl:has-[[data-claims-dock]]:max-w-[86rem]"><PageTransition>{children}</PageTransition></main>
          </PlayerDialogProvider>
        </DecisionPrefsProvider>
        <Toaster position="bottom-center" />
      </body>
    </html>
  );
}
