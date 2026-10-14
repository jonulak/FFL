import type { Metadata } from "next";
import { Barlow_Condensed, Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { DecisionPrefsProvider } from "@/components/decision-prefs";
import { Nav } from "@/components/nav";
import { PageTransition } from "@/components/page-transition";
import { PlayerDialogProvider } from "@/components/player-dialog";
import { SwRegister } from "@/components/sw-register";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
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
        <TooltipProvider delay={400}>
          <DecisionPrefsProvider prefs={prefs}>
            <PlayerDialogProvider>
              <Nav />
              {/* one 64rem column on every page, the nav's width; the Players page's claims dock stands outside it */}
              <main className="mx-auto max-w-5xl px-3 py-4"><PageTransition>{children}</PageTransition></main>
            </PlayerDialogProvider>
          </DecisionPrefsProvider>
        </TooltipProvider>
        <Toaster position="bottom-center" />
      </body>
    </html>
  );
}
