"use client";

import { useRouter } from "@/components/league-link";
import { useEffect, useOptimistic, useState, useTransition, ViewTransition } from "react";
import { clientGet } from "@/app/api-client";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { NewsPage, PlayerNews, Story } from "@/lib/api";
import { BOARD_CARD, LABEL } from "@/lib/board";
import { filterHref, NEWS_FILTERS, ownerTag, type NewsFilter } from "@/lib/news";
import { NAME_LINK } from "@/lib/player-log";
import { Spinner } from "./motion";
import { NewsSearch } from "./news-search";
import { NewsStory } from "./news-story";
import { PlayerTrigger } from "./player-dialog";

// The wire as one board card, newest update first: every story a row, divided by ink hairlines (the Transactions
// week card's shape). "Older" asks for the page before the last story shown and lays it under the rest.
function useOlder(initial: NewsPage, path: string) {
  const [stories, setStories] = useState(initial.stories);
  const [next, setNext] = useState(initial.next_before);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  // a new first page (another filter, a refresh) starts the list again (set in render, React's pattern for state off a prop)
  const [seen, setSeen] = useState(initial);
  if (seen !== initial) {
    setSeen(initial);
    setStories(initial.stories);
    setNext(initial.next_before);
    setFailed(false);
  }
  const older = () => {
    if (!next || loading) return;
    setLoading(true);
    setFailed(false);
    clientGet<NewsPage>(`${path}${path.includes("?") ? "&" : "?"}before=${encodeURIComponent(next)}`)
      .then((d) => {
        // a story updated between the two reads can come round again: keep its first place
        setStories((had) => [...had, ...d.stories.filter((s) => !had.some((h) => h.id === s.id))]);
        setNext(d.next_before);
      })
      .catch(() => setFailed(true))
      .finally(() => setLoading(false));
  };
  return { stories, next, loading, failed, older };
}

// The read that failed, said plainly: the feed still serves, without owner tags or names.
function Notices({ d }: { d: NewsPage }) {
  if (!d.owners_error && !d.index_error) return null;
  return (
    <div className="space-y-0.5 text-xs leading-5 text-muted-foreground">
      {d.owners_error && <p>Owner tags are unavailable: ESPN&apos;s rosters didn&apos;t load, so no player is tagged mine, a free agent or another team&apos;s. Until they do, My players and Other rosters find nothing and Free agents shows every story.</p>}
      {d.index_error && <p>Player names are unavailable: the player list didn&apos;t load, so some show as ids.</p>}
    </div>
  );
}

function List({ stories, focus, target, tagged, filter = "all", empty, next, loading, failed, older, pending = false }: {
  stories: Story[]; focus?: number; target?: number | null; tagged: boolean; filter?: NewsFilter; empty: string;
  next: string | null; loading: boolean; failed: boolean; older: () => void; pending?: boolean;
}) {
  return (
    <div className="space-y-3">
      <Card data-pending={pending || undefined} className={`${BOARD_CARD} pending-dim overflow-hidden`}>
        <ViewTransition update="swap" default="none">
          <CardContent className="px-0 py-0">
            {stories.length === 0
              ? <p className="px-4 py-6 text-center text-xs text-muted-foreground">{empty}</p>
              : (
                <ul className="divide-y divide-foreground/10">
                  {stories.map((s) => <NewsStory key={s.id} s={s} focus={focus} target={s.id === target} tagged={tagged} filter={filter} />)}
                </ul>
              )}
          </CardContent>
        </ViewTransition>
      </Card>
      {next && (
        <div className="flex flex-col items-center gap-1.5">
          <Button variant="outline" onClick={older} disabled={loading}
            className="h-10 border-foreground/15 px-4 font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] sm:h-8">
            Older<Spinner on={loading} gap="-ml-1.5" className="size-3.5" />
          </Button>
          {failed && <p className="text-xs text-muted-foreground">Older stories didn&apos;t load. Try again.</p>}
        </div>
      )}
    </div>
  );
}

const EMPTY: Record<NewsFilter, string> = {
  all: "No stories yet. The news desk runs every ten minutes.",
  mine: "No news on your players yet.",
  fa: "No news on free agents yet.",
  others: "No news on other rosters' players yet.",
};

// /news: the search, the four filters (URL state, ?filter=) and the feed. Stories that move nobody (every row ●) are
// left out by the API, so each page fills with stories that move someone.
// `target` is ?story=, the story a push opened: scrolled to and washed in ink. `pinned` is that story read on its own
// (GET /news/story/{id}); when the first page does not hold it (the feed hides it, the filter excludes it, or it is
// further back) it stands first, so a push always lands on its story.
export function NewsFeed({ data, filter, target, pinned }: {
  data: NewsPage; filter: NewsFilter; target: number | null; pinned: Story | null;
}) {
  const router = useRouter();
  const [pending, start] = useTransition();
  // the tab pressed takes at once; the page's own value takes over when it lands
  const [shown, setShown] = useOptimistic(filter);
  const { stories, next, loading, failed, older } = useOlder(data, `/news?filter=${filter}`);
  const pinOn = pinned != null && !data.stories.some((s) => s.id === pinned.id);
  const list = pinOn ? [pinned, ...stories.filter((s) => s.id !== pinned.id)] : stories;
  const go = (f: NewsFilter) => start(() => {
    setShown(f);
    router.replace(filterHref(f, target), { scroll: false });
  });
  useEffect(() => {
    if (target == null) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document.getElementById(`story-${target}`)?.scrollIntoView({ block: "start", behavior: reduce ? "auto" : "smooth" });
  }, [target]);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-x-3 gap-y-3">
        <h1 className="font-display text-4xl font-bold leading-none tracking-tight">News</h1>
        {/* on the title line from `sm`; on the phone the field takes the full width under it */}
        <div className="w-full sm:ml-auto sm:w-auto"><NewsSearch /></div>
      </div>
      {/* the chyron strip of the nav and the Players positions (pos-tabs): shadcn Tabs, line variant, the chosen filter
          in full ink over a bar that slides to it; it scrolls on a phone too narrow for the four */}
      <Tabs value={shown} onValueChange={(v) => {
        const f = v as NewsFilter;
        if (f !== filter) go(f);
      }} aria-label="Whose news">
        <TabsList variant="line" indicatorInset="0.625rem" className="h-10 w-full justify-start gap-0 overflow-x-auto overflow-y-hidden border-b border-foreground/12 p-0 [scrollbar-width:none]">
          {NEWS_FILTERS.map(([f, label]) => (
            <TabsTrigger key={f} value={f}
              className="h-full flex-none rounded-none px-1.5 font-display text-[0.875rem] font-semibold uppercase tracking-[0.08em] sm:px-2.5 sm:text-[0.9375rem] sm:tracking-[0.12em] text-foreground/55 hover:text-foreground/85 data-active:text-foreground dark:text-foreground/55 dark:hover:text-foreground/85 dark:data-active:text-foreground">
              {label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      <Notices d={data} />
      {target != null && !pinned && !stories.some((s) => s.id === target) && (
        <p className="text-xs text-muted-foreground">The story from the notification is no longer in the feed.</p>
      )}
      <List stories={list} target={target} tagged={!data.owners_error} filter={filter} next={next} loading={loading} failed={failed} older={older} pending={pending}
        empty={EMPTY[filter]} />
    </div>
  );
}

// /news/player/<id>: his header, then every story that names him in the window, his row opened out into his line.
// `bell` is the push mute (news-mute.tsx), at the header's end.
export function PlayerNewsView({ data, days, bell }: { data: PlayerNews; days: number; bell?: React.ReactNode }) {
  const p = data.player;
  const { stories, next, loading, failed, older } = useOlder(data, `/news/player/${p.player_id}?days=${days}`);
  const owner = p.mine ? "on my roster" : p.owner ? (p.owner.name ?? ownerTag(p)) : data.owners_error ? null : "free agent";
  const seed = { player_id: p.player_id, name: p.name, pos: p.pos ?? "", team: p.team ?? "" };
  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="font-display text-4xl font-bold leading-none tracking-tight">
            <PlayerTrigger p={seed} className={`rounded-sm text-left ${NAME_LINK}`}>{p.name}</PlayerTrigger>
          </h1>
          <p className="mt-1.5 flex flex-wrap items-center gap-x-1.5 text-xs leading-4 text-muted-foreground">
            {p.pos && <span className="font-semibold text-foreground/80">{p.pos}</span>}
            {p.pos && p.team && <span aria-hidden>·</span>}
            {p.team && <span>{p.team}</span>}
            {owner && <><span aria-hidden>·</span><span className={p.mine ? "font-medium text-foreground/85" : ""}>{owner}</span></>}
          </p>
        </div>
        {bell}
      </div>
      <p className={LABEL}>
        {stories.length}{next ? "+" : ""} {stories.length === 1 && !next ? "story" : "stories"} · last {days} days
      </p>
      <Notices d={data} />
      <List stories={stories} focus={p.player_id} tagged={!data.owners_error} empty={`No news on him in the last ${days} days.`} next={next} loading={loading} failed={failed} older={older} />
    </div>
  );
}
