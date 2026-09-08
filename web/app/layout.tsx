import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Nav } from "@/components/nav";

const geistSans = Geist({ variable: "--font-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = { title: "FFL", description: "2026 season cockpit" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // The font variables belong on <html>: globals.css applies `font-sans` there, and
  // `--font-sans` has to be defined on the same element or above it.
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable}`} suppressHydrationWarning>
      <body className="min-h-dvh bg-background text-foreground antialiased">
        <Nav />
        <main className="mx-auto max-w-5xl px-3 py-4">{children}</main>
      </body>
    </html>
  );
}
