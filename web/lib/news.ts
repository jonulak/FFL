import type { NewsCite, NewsEffect, NewsRow, PlayerNews, Story } from "./api";

// The News page's filters, in the URL as ?filter= (GET /news takes the same four).
export type NewsFilter = "all" | "mine" | "fa" | "others";
export const NEWS_FILTERS: [NewsFilter, string][] = [["all", "All"], ["mine", "My players"], ["fa", "Free agents"], ["others", "Other rosters"]];

export function parseNewsFilter(raw: string | string[] | undefined): NewsFilter {
  return NEWS_FILTERS.some(([f]) => f === raw) ? (raw as NewsFilter) : "all";
}

// A route segment or ?story= as an id; null when it is not a positive integer.
export function parseId(raw: string | string[] | undefined): number | null {
  const n = Number(raw);
  return typeof raw === "string" && raw !== "" && Number.isInteger(n) && n > 0 ? n : null;
}

// The page's URL: the filter and a push's story.
export function filterHref(f: NewsFilter, story: number | null = null): string {
  const q = new URLSearchParams();
  if (f !== "all") q.set("filter", f);
  if (story != null) q.set("story", String(story));
  const s = q.toString();
  return s ? `/news?${s}` : "/news";
}

export const playerNewsHref = (id: number) => `/news/player/${id}`;

// The effect in words: the page says it in colour (news-story.tsx), the push in glyphs (ffl/newsdesk/notices.py).
export const EFFECT_WORD: Record<NewsEffect, string> = { up: "up", down: "down", none: "no change" };

// Who holds him: mine, a free agent, or the other team by its abbreviation.
export function ownerTag(r: Pick<NewsRow, "owner" | "mine">): string {
  if (r.mine) return "Mine";
  if (!r.owner) return "FA";
  return r.owner.abbrev || r.owner.name || `Team ${r.owner.team_id}`;
}

// A story's chips: only the players it moves; one it merely names is left off. Under My players only my own of
// those, under Free agents only the free agents; All and Other rosters show every one.
export function chipRows(rows: NewsRow[], filter: NewsFilter = "all"): NewsRow[] {
  return rows.filter((r) => r.effect !== "none"
    && (filter === "mine" ? r.mine : filter === "fa" ? !r.mine && !r.owner : true));
}

// The card's stamp, the same in the feed and the player dialog: always the newest source and its age, since the feed
// is ordered by it (a first source's age on a story another outlet just added to reads out of order). A story that had
// a real update (rev > 1; another outlet repeating the news is none) also says "Updated". The first source, the
// scoop, stays among the story's sources.
export function storyStamp(s: Pick<Story, "updated" | "first" | "latest" | "at">): { updated: boolean; cite: NewsCite | null; at: string } {
  const cite = s.latest ?? s.first;
  return { updated: s.updated, cite, at: cite?.at ?? s.at };
}

export function statusLabel(s: Story["status"]): string | null {
  return s === "retracted" ? "Retracted" : s === "corrected" ? "Corrected" : null;
}

// The player dialog's Recent news: his newest three stories off GET /news/player/{id}, each with his own row (the
// only one that page sends). A failed read is no news.
export const RECENT = 3;
export type Recent = { story: Story; row: NewsRow }[];
export function recentFor(d: PlayerNews | null): Recent {
  if (!d) return [];
  const out: Recent = [];
  for (const story of d.stories) {
    const row = story.players.find((r) => r.player_id === d.player.player_id);
    if (row) out.push({ story, row });
    if (out.length === RECENT) break;
  }
  return out;
}
