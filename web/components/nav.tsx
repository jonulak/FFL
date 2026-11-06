import { LeagueSwitcher } from "@/components/league-switcher";
import { NavLinks } from "@/components/nav-links";
import { NotificationsBell } from "@/components/notifications-bell";
import { SettingsLink } from "@/components/settings-link";
import type { LeagueEntry } from "@/lib/api";

// A full page load (a league switch, a reload) paints the phone's tab strip scrolled to its start, and NavLinks only
// brings the current tab into view once it hydrates (~500 ms on a warm load), so a tab past the first few jumped
// across the strip. This runs as the parser reaches it, before the first paint: the current tab centred, as NavLinks
// centres it, so its effect then finds nothing to move.
const CENTER_TAB = `try{var a=document.currentScript.parentNode.querySelector('nav[aria-label="Pages"] a[aria-current="page"]'),l=a&&a.closest('[role="tablist"]');if(l){var r=a.getBoundingClientRect(),s=l.getBoundingClientRect();l.scrollLeft+=r.left+r.width/2-(s.left+s.width/2)}}catch(e){}`;

// The board's top rule: the wordmark (the league switcher), a hairline, the page tabs, and the notifications bell and
// Settings gear pushed to the right edge. The header is the field at 92% over a blur so the cards slide under it; its bottom rule is
// ink at 12%, the same hairline the section headings run. Below `lg` (phone, iPad) it is two rows (--nav-h,
// globals.css): the switcher with the gear at the far end over the full-width tab strip, since the league's name and
// the eight tabs do not share the row (an iPad's 9rem truncated even home's name); from `lg` the one 48px rule. Every league has every tab and the gear, each link carrying the open league.
export function Nav({ leagues, league, unread }: { leagues: LeagueEntry[]; league: LeagueEntry | null; unread: number | null }) {
  const away = league != null && !league.home;
  return (
    <header className="sticky top-0 z-10 border-b border-foreground/12 bg-background/92 backdrop-blur-md">
      <div className="mx-auto flex h-(--nav-h) max-w-5xl flex-wrap items-stretch gap-x-3 px-3 lg:flex-nowrap">
        <div className="flex h-11 min-w-0 flex-1 items-center lg:h-auto lg:flex-none">
          <LeagueSwitcher leagues={leagues} open={league} />
        </div>
        <span aria-hidden className="my-3.5 hidden w-px shrink-0 bg-foreground/12 lg:block" />
        <NavLinks className="order-last h-10 basis-full lg:order-none lg:h-auto lg:basis-auto" league={away ? league.key : null} />
        <div className="ml-auto flex h-11 shrink-0 items-center gap-0.5 lg:h-auto lg:pl-2">
          <NotificationsBell unread={unread} openLeague={league?.key ?? null} />
          <SettingsLink />
        </div>
      </div>
      <script dangerouslySetInnerHTML={{ __html: CENTER_TAB }} />
    </header>
  );
}
