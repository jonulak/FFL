import { LeagueSwitcher } from "@/components/league-switcher";
import { NavLinks } from "@/components/nav-links";
import { SettingsLink } from "@/components/settings-link";
import type { LeagueEntry } from "@/lib/api";

// The board's top rule: the wordmark (the league switcher), a hairline, the page tabs, and the Settings gear pushed
// to the right edge. The header is the field at 92% over a blur so the cards slide under it; its bottom rule is
// ink at 12%, the same hairline the section headings run. Another league shows only its own tabs and no gear
// (Settings is home's until 4.10).
export function Nav({ leagues, league }: { leagues: LeagueEntry[]; league: LeagueEntry | null }) {
  const away = league != null && !league.home;
  return (
    <header className="sticky top-0 z-10 border-b border-foreground/12 bg-background/92 backdrop-blur-md">
      <div className="mx-auto flex h-12 max-w-5xl items-stretch gap-3 px-3">
        <LeagueSwitcher leagues={leagues} open={league} />
        <span aria-hidden className="my-3.5 w-px shrink-0 bg-foreground/12" />
        <NavLinks pages={away ? league.pages : null} league={away ? league.key : null} />
        {!away && <div className="ml-auto flex shrink-0 items-center gap-0.5 pl-2"><SettingsLink /></div>}
      </div>
    </header>
  );
}
