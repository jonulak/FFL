import { NextResponse, type NextRequest } from "next/server";
import { API_BASE } from "@/lib/api";
import { checksSetup, needsSignIn, PATH_HEADER, sessionCookie, SETUP_PATH, signInHref } from "@/lib/auth";
import { decide, LEAGUE_COOKIE, LEAGUE_HEADER, leagueOf } from "@/lib/league";

// Whether the server still has no login (GET /setup/status), asked at most every 10 s rather than on every page request.
// Only "set up" is cached: a cached "needed" would outlive the setup and bounce /setup (now "/") and "/" into each
// other for up to 10 s. Unreachable or refused: no (the sign-in page then shows the error), not cached.
let setUpAt = 0;
async function setupNeeded(): Promise<boolean> {
  if (Date.now() - setUpAt < 10_000) return false;
  try {
    const r = await fetch(`${API_BASE}/setup/status`, { cache: "no-store", signal: AbortSignal.timeout(5_000) });
    if (!r.ok) return false;
    if ((await r.json()).needs_setup === true) return true;
    setUpAt = Date.now();
    return false;
  } catch {
    return false;
  }
}

// The open league (ffl-soc.4.13, lib/league.ts): every page request, the soft navigations' RSC requests included,
// carries ?league= into the x-ffl-league request header that lib/api.ts's server calls read; the start page opens
// the last league used, and a league page remembers its league in a cookie (a year: the Home Screen app's memory).
export async function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const signedIn = request.cookies.has(sessionCookie());
  // A server nobody has set up yet: a signed-out visitor, or /signin with a stale cookie, goes to first-run setup.
  if (checksSetup(pathname, signedIn) && await setupNeeded()) {
    return NextResponse.redirect(new URL(SETUP_PATH, request.url));
  }
  // Signed out first (ffl-soc.6.2.1, lib/auth.ts): any page request without the session cookie, a soft navigation's
  // too, goes to sign in and comes back here after; the league cookie is left as it was. Presence only: the API checks it.
  if (needsSignIn(pathname, signedIn)) {
    return NextResponse.redirect(new URL(signInHref(pathname, search), request.url));
  }
  // a full page load: the browser says so (Sec-Fetch-Dest); one that does not, by asking for HTML. A soft navigation
  // and a prefetch are fetches (dest "empty"), whose Next headers the proxy never sees.
  const dest = request.headers.get("sec-fetch-dest");
  const document = dest ? dest === "document" : (request.headers.get("accept") ?? "").includes("text/html");
  const d = decide(pathname, search, request.cookies.get(LEAGUE_COOKIE)?.value ?? null, document);
  if (d.redirect) return NextResponse.redirect(new URL(d.redirect, request.url));
  const headers = new Headers(request.headers);
  headers.delete(LEAGUE_HEADER); // only the URL names the league, never a header a client sent
  const league = leagueOf(search);
  if (league) headers.set(LEAGUE_HEADER, league);
  // the page this render is for: lib/api.ts sends a call the API refused (an expired session) to sign in, back here
  headers.set(PATH_HEADER, `${pathname}${search}`);
  const res = NextResponse.next({ request: { headers } });
  if (d.cookie !== undefined) {
    if (d.cookie) res.cookies.set(LEAGUE_COOKIE, d.cookie, { path: "/", maxAge: 365 * 86400, sameSite: "lax" });
    else res.cookies.delete(LEAGUE_COOKIE);
  }
  return res;
}

// pages only: never the API rewrite, Next's own assets, or the files in public/
export const config = {
  matcher: ["/((?!api/|_next/|favicon.ico|apple-icon.png|manifest.webmanifest|sw.js|.*\\.(?:png|jpg|svg|ico|js|css|txt|webmanifest)$).*)"],
};
