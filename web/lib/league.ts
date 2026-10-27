// The open league (ffl-soc.4.13): it rides in the page URL as ?league=<registry key>, as on the API, and no
// parameter is the home league. web/proxy.ts copies it into a request header for the server's API calls
// (lib/api.ts) and remembers the last league used in a cookie for the start page; app/api-client.ts reads it off
// the browser's URL. Pure: everything here is tested in league.test.ts.
export const LEAGUE_PARAM = "league";
export const LEAGUE_COOKIE = "ffl-league";
export const LEAGUE_HEADER = "x-ffl-league";
// sessionStorage: set by the switcher just before a league switch's full page load; the next document reads it before
// its first paint (app/layout.tsx ARRIVE_SCRIPT) and plays the arrival (globals.css, "League switch").
export const LEAGUE_ARRIVE = "ffl-league-arrive";

// League data pages: opening one is "using" its league (the Matchup page "/" too, once it names one). NFL, News and
// Settings are global and leave it alone.
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

// An in-app link or router move made from a page of `league` (components/league-link.tsx): it stays in that league.
// A link that names a league already (a push's target, the switcher) keeps it; an API path, an outside or a relative
// link is left alone. Home: every link as it was.
export function leagueHref(href: string, league: string | null): string {
  if (!league || !href.startsWith("/") || href.startsWith("//") || href.startsWith("/api/")) return href;
  if (leagueOf(href.split("#", 1)[0].split("?", 2)[1] ?? "")) return href;
  return withLeague(href, league);
}

// The pages a switch keeps the query of: every league's (NFL, News, the device's Settings), and Players, whose views,
// positions and filters are every league's too (0024).
const SHARED_PAGES = ["/nfl", "/news", "/plays", "/settings", "/players"];

// Where a league switch lands, before the new league is named on it (the switcher): the same page in the other
// league. A league's own page keeps only its path (its weeks and filters are that league's); a page of one
// team or one week (/team/<id>, /league/<week>[/<team>]) its list, since the other league's ids and weeks differ; a
// shared page stays exactly where it is, less the league.
export function switchPath(path: string, search: string): string {
  if (path !== "/settings/league" && SHARED_PAGES.some((p) => path === p || path.startsWith(`${p}/`))) {
    const q = new URLSearchParams(search);
    q.delete(LEAGUE_PARAM);
    const rest = q.toString();
    return rest ? `${path}?${rest}` : path;
  }
  if (path.startsWith("/team/") || path.startsWith("/league/")) return "/league";
  return path;
}

// The league a query string names; null for home (none, or empty).
export function leagueOf(search: string): string | null {
  return new URLSearchParams(search).get(LEAGUE_PARAM) || null;
}

// The Home Screen app's launch (app/manifest.ts start_url): home's Roster, marked so the proxy can tell the launch
// from a link to home's Roster (a push, a tab), which must open home whatever league was used last.
export const START_URL = "/roster?launch=1";

// What the proxy does with a page request: the start page ("/" and the Home Screen launch) opens the last league used;
// a league page remembers its league (cookie: the key, null to forget it: home). Undefined fields: nothing to do.
// Only a full page load (`document`) counts: Next strips its prefetch and RSC headers from the proxy's request, so a
// soft navigation or a <Link> prefetch would otherwise move the last league, or redirect under the old layout.
export type ProxyDecision = { redirect?: string; cookie?: string | null };

export function decide(path: string, search: string, cookie: string | null, document: boolean): ProxyDecision {
  if (!document) return {};
  const league = leagueOf(search);
  const start = (path === "/" && !search) || `${path}${search}` === START_URL;
  if (start) return cookie ? { redirect: withLeague("/roster", cookie) } : {};
  if (path !== "/" && !LEAGUE_PAGES.some((p) => path === p || path.startsWith(`${p}/`))) return {};
  if ((league ?? null) === (cookie || null)) return {};
  return { cookie: league };
}

// The nav's tabs for the open league: every tab in every league, another league's each href carrying it (the global
// NFL and News too, so the next tab tapped from them is still that league's). [href the tab is matched on, label,
// href it links to].
export function tabsFor<T extends readonly (readonly [string, string])[]>(tabs: T, league: string | null): [string, string, string][] {
  return tabs.map(([href, label]) => [href, label, withLeague(href, league)]);
}

// The page a signed-in user lands on with no league at all (ffl-soc.6.2.2): the API answers 404 no_leagues.
export const WELCOME_PATH = "/welcome";

// A server call the API answered 404 for the league itself (ffl-soc.6.2.2): a league the user is not in (a stale
// last-league cookie, a link from someone else) opens the same page in their default league, the ?league= dropped;
// a user with no league at all goes to the welcome page. Any other answer is the caller's (null).
export function leagueMissRedirect(status: number, body: unknown, path: string, search: string): string | null {
  if (status !== 404 || typeof body !== "object" || body === null) return null;
  const detail = (body as { detail?: unknown }).detail;
  const error = typeof detail === "object" && detail !== null ? (detail as { error?: unknown }).error : null;
  if (error === "no_leagues") return path === WELCOME_PATH ? null : WELCOME_PATH;
  if (error !== "unknown_league" || !leagueOf(search)) return null;
  const q = new URLSearchParams(search);
  q.delete(LEAGUE_PARAM);
  const rest = q.toString();
  return rest ? `${path}?${rest}` : path;
}
