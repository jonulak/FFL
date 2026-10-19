import type { NewsEffect, NewsRow, Story } from "./api";

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
export function splitRows(rows: NewsRow[]): { moved: NewsRow[]; none: NewsRow[] } {
  return { moved: rows.filter((r) => r.effect !== "none"), none: rows.filter((r) => r.effect === "none") };
}

export function statusLabel(s: Story["status"]): string | null {
  return s === "retracted" ? "Retracted" : s === "corrected" ? "Corrected" : null;
}
