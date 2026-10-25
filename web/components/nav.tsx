import { LeagueSwitcher } from "@/components/league-switcher";
import { NavLinks } from "@/components/nav-links";
import { SettingsLink } from "@/components/settings-link";
import type { LeagueEntry } from "@/lib/api";

// The board's top rule: the wordmark (the league switcher), a hairline, the page tabs, and the Settings gear pushed
// to the right edge. The header is the field at 92% over a blur so the cards slide under it; its bottom rule is
// ink at 12%, the same hairline the section headings run. On the phone it is two rows (--nav-h, globals.css): the
// switcher with the gear at the far end over the full-width tab strip, since the league's name and the tabs do not
// share 360px; from `sm` the one 48px rule. Another league shows only its own tabs and no gear (Settings is home's
// until 4.10).
export function Nav({ leagues, league }: { leagues: LeagueEntry[]; league: LeagueEntry | null }) {
  const away = league != null && !league.home;
  return (
    <header className="sticky top-0 z-10 border-b border-foreground/12 bg-background/92 backdrop-blur-md">
      <div className="mx-auto flex h-(--nav-h) max-w-5xl flex-wrap items-stretch gap-x-3 px-3 sm:flex-nowrap">
        <div className="flex h-11 min-w-0 flex-1 items-center sm:h-auto sm:flex-none">
          <LeagueSwitcher leagues={leagues} open={league} />
        </div>
        <span aria-hidden className="my-3.5 hidden w-px shrink-0 bg-foreground/12 sm:block" />
        <NavLinks className="order-last h-10 basis-full sm:order-none sm:h-auto sm:basis-auto" pages={away ? league.pages : null} league={away ? league.key : null} />
        {!away && <div className="ml-auto flex h-11 shrink-0 items-center gap-0.5 sm:h-auto sm:pl-2"><SettingsLink /></div>}
      </div>
    </header>
  );
}
