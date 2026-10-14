import Link from "next/link";
import { NavLinks } from "@/components/nav-links";
import { SettingsLink } from "@/components/settings-link";

// The board's top rule: wordmark, a hairline, the page tabs, and the Settings gear pushed to the right edge.
// The header is the field at 92% over a blur so the cards slide under it; its bottom rule is
// ink at 12%, the same hairline the section headings run. Its row is as wide as <main>: 64rem, and 86rem
// from `xl` when the page carries the claims dock (the Players page), the same `:has` test the layout's <main> runs.
export function Nav() {
  return (
    <header className="sticky top-0 z-10 border-b border-foreground/12 bg-background/92 backdrop-blur-md">
      <div className="mx-auto flex h-12 max-w-5xl items-stretch gap-3 px-3 xl:[body:has([data-claims-dock])_&]:max-w-[86rem]">
        <Link href="/" className="flex items-center font-display text-[1.6rem] font-bold leading-none tracking-tight" aria-label="FFL, Matchup">
          FFL
        </Link>
        <span aria-hidden className="my-3.5 w-px shrink-0 bg-foreground/12" />
        <NavLinks />
        <div className="ml-auto flex shrink-0 items-center gap-0.5 pl-2"><SettingsLink /></div>
      </div>
    </header>
  );
}
