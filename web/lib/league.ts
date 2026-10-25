// The open league (ffl-soc.4.13): it rides in the page URL as ?league=<registry key>, as on the API, and no
// parameter is the home league. web/proxy.ts copies it into a request header for the server's API calls
// (lib/api.ts) and remembers the last league used in a cookie for the start page; app/api-client.ts reads it off
// the browser's URL. Pure: everything here is tested in league.test.ts.
export const LEAGUE_PARAM = "league";
export const LEAGUE_COOKIE = "ffl-league";
export const LEAGUE_HEADER = "x-ffl-league";

// League data pages: opening one is "using" its league. NFL, News and Settings are global and leave it alone.
const LEAGUE_PAGES = ["/roster", "/players", "/waivers", "/league", "/transactions", "/decisions", "/team"];

// `href` naming `league` (replacing any league it named); null or "" is home: the href as it was.
export function withLeague(href: string, league: string | null): string {
  if (!league) return href;
  const [rest, hash = ""] = href.split("#", 2);
  const [path, query = ""] = rest.split("?", 2);
  const p = new URLSearchParams(query);
  p.delete(LEAGUE_PARAM);
  p.set(LEAGUE_PARAM, league);
  return `${path}?${p}${hash ? `#${hash}` : ""}`;
}

// The league a query string names; null for home (none, or empty).
export function leagueOf(search: string): string | null {
  return new URLSearchParams(search).get(LEAGUE_PARAM) || null;
}

// What the proxy does with a page request: the start page ("/", the Home Screen launch) opens the last league used;
// a league page remembers its league (cookie: the key, null to forget it: home). Undefined fields: nothing to do.
export type ProxyDecision = { redirect?: string; cookie?: string | null };

export function decide(path: string, search: string, cookie: string | null): ProxyDecision {
  const league = leagueOf(search);
  if (path === "/") return !league && !search && cookie ? { redirect: withLeague("/roster", cookie) } : {};
  if (!LEAGUE_PAGES.some((p) => path === p || path.startsWith(`${p}/`))) return {};
  if ((league ?? null) === (cookie || null)) return {};
  return { cookie: league };
}

// The nav's tabs for the open league: home (pages null) all of them; another league the pages GET /leagues gives it,
// each href carrying the league. [href the tab is matched on, label, href it links to].
export function tabsFor<T extends readonly (readonly [string, string])[]>(
  tabs: T, pages: string[] | null, league: string | null,
): [string, string, string][] {
  return tabs
    .filter(([href]) => pages == null || pages.includes(href.replace(/^\//, "")))
    .map(([href, label]) => [href, label, withLeague(href, league)]);
}
