import { NextResponse, type NextRequest } from "next/server";
import { decide, LEAGUE_COOKIE, LEAGUE_HEADER, leagueOf } from "@/lib/league";

// The open league (ffl-soc.4.13, lib/league.ts): every page request, the soft navigations' RSC requests included,
// carries ?league= into the x-ffl-league request header that lib/api.ts's server calls read; the start page opens
// the last league used, and a league page remembers its league in a cookie (a year: the Home Screen app's memory).
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const d = decide(pathname, search, request.cookies.get(LEAGUE_COOKIE)?.value ?? null);
  if (d.redirect) return NextResponse.redirect(new URL(d.redirect, request.url));
  const headers = new Headers(request.headers);
  headers.delete(LEAGUE_HEADER); // only the URL names the league, never a header a client sent
  const league = leagueOf(search);
  if (league) headers.set(LEAGUE_HEADER, league);
  const res = NextResponse.next({ request: { headers } });
  // a prefetch is not a visit: it must not move the last league used
  const prefetch = request.headers.get("sec-purpose")?.includes("prefetch");
  if (d.cookie !== undefined && !prefetch) {
    if (d.cookie) res.cookies.set(LEAGUE_COOKIE, d.cookie, { path: "/", maxAge: 365 * 86400, sameSite: "lax" });
    else res.cookies.delete(LEAGUE_COOKIE);
  }
  return res;
}

// pages only: never the API rewrite, Next's own assets, or the files in public/
export const config = {
  matcher: ["/((?!api/|_next/|favicon.ico|apple-icon.png|manifest.webmanifest|sw.js|.*\\.(?:png|jpg|svg|ico|js|css|txt|webmanifest)$).*)"],
};
