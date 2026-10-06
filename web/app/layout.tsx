import type { Metadata } from "next";
import { Barlow_Condensed, Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Nav } from "@/components/nav";
import { PlayerDialogProvider } from "@/components/player-dialog";

const geistSans = Geist({ variable: "--font-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
// The board face: condensed, for names and numerals, the way a broadcast lineup graphic sets them.
const barlow = Barlow_Condensed({ variable: "--font-barlow", subsets: ["latin"], weight: ["500", "600", "700"] });

export const metadata: Metadata = { title: "FFL", description: "2026 season cockpit" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // The font variables belong on <html>: globals.css applies `font-sans` there, and
  // `--font-sans` has to be defined on the same element or above it.
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} ${barlow.variable}`} suppressHydrationWarning>
      <body className="min-h-dvh bg-background text-foreground antialiased">
        {/* the season modal: one dialog for the app, opened from any player row */}
        <PlayerDialogProvider>
          <Nav />
          {/* the Waivers page with its claims dock gets 86rem from `xl`, so the table still fits beside it */}
          <main className="mx-auto max-w-5xl px-3 py-4 xl:has-[[data-claims-dock]]:max-w-[86rem]">{children}</main>
        </PlayerDialogProvider>
      </body>
    </html>
  );
}
