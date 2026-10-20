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

// The page's URL: the filter and, when no-change stories are shown, `quiet=1` (the feed hides them by default).
export function filterHref(f: NewsFilter, quiet = false, story: number | null = null): string {
  const q = new URLSearchParams();
  if (f !== "all") q.set("filter", f);
  if (quiet) q.set("quiet", "1");
  if (story != null) q.set("story", String(story));
  const s = q.toString();
  return s ? `/news?${s}` : "/news";
}

export const playerNewsHref = (id: number) => `/news/player/${id}`;

// The push's glyphs (ffl/newsdesk/notices.py): the arrow is the effect, never a colour (the Three Verdicts Rule).
const ARROW: Record<NewsEffect, string> = { up: "▲", down: "▼", none: "●" };
export const arrow = (e: NewsEffect) => ARROW[e];
export const EFFECT_WORD: Record<NewsEffect, string> = { up: "up", down: "down", none: "no change" };

// Who holds him: mine, a free agent, or the other team by its abbreviation.
export function ownerTag(r: Pick<NewsRow, "owner" | "mine">): string {
  if (r.mine) return "Mine";
  if (!r.owner) return "FA";
  return r.owner.abbrev || r.owner.name || `Team ${r.owner.team_id}`;
}

// The arrows show; the ● rows wait behind "+k no change".
// `onlyMine` (the My players filter): only my own rows, every one out in the open, none behind "+k no change".
export function splitRows(rows: NewsRow[], onlyMine = false): { moved: NewsRow[]; none: NewsRow[] } {
  if (onlyMine) return { moved: rows.filter((r) => r.mine), none: [] };
  return { moved: rows.filter((r) => r.effect !== "none"), none: rows.filter((r) => r.effect === "none") };
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
