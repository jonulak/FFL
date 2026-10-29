// Signing in (ffl-soc.6.2.1). The API owns the session (cookie ffl_session, HttpOnly, set through the /api rewrite) and
// answers 401 {"error": "signed_out"} without one; the web only routes the visitor: web/proxy.ts sends a page load with
// no cookie to /signin, lib/api.ts and app/api-client.ts send a call the API refused there. Pure: tested in auth.test.ts.
export const SESSION_COOKIE = "ffl_session";
// The session cookie's name as the API reads it (auth.session_cookie): FFL_SESSION_COOKIE, else ffl_session. A browser
// shares cookies across ports, so two worktree test servers on one host each name their own; production leaves it unset.
// Server side only (the proxy and lib/api.ts run on Node and read the env at request time).
export function sessionCookie(env: Record<string, string | undefined> = process.env): string {
  return env.FFL_SESSION_COOKIE || SESSION_COOKIE;
}
export const SIGNIN_PATH = "/signin";
// The privacy policy (#135): public, since the extension stores link to it and a visitor reads it before signing up.
export const PRIVACY_PATH = "/privacy";
// The page a server render is for (path + query), set by web/proxy.ts so lib/api.ts can send a refused call back to it.
export const PATH_HEADER = "x-ffl-path";

// A page request that must sign in first: no session cookie (its presence only; the API checks it), and never /signin
// or the privacy policy.
export function needsSignIn(pathname: string, hasSession: boolean): boolean {
  return !hasSession && pathname !== SIGNIN_PATH && pathname !== PRIVACY_PATH;
}

// Where to land after signing in: a path on this site only, the API's safe_next (ffl/auth.py) in the browser's terms:
// `//host` is another origin, and a browser reads a backslash as a slash and drops tabs and newlines.
export function safeNext(raw: string | null | undefined): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\") || /[\x00-\x1f]/.test(raw)) return "/";
  return raw;
}

// The sign-in page, sent back to `pathname` + `search` (the whole query: ?league= survives) once signed in. The start
// page needs no next, and /signin never names itself.
export function signInHref(pathname: string, search: string): string {
  if (pathname === SIGNIN_PATH) return SIGNIN_PATH;
  const q = search.startsWith("?") ? search.slice(1) : search;
  const next = safeNext(q ? `${pathname}?${q}` : pathname);
  return next === "/" ? SIGNIN_PATH : `${SIGNIN_PATH}?next=${encodeURIComponent(next)}`;
}

// A refused call: the API's signed-out answer goes to sign in (back to the page it came from); any other is the caller's.
export function signedOutRedirect(status: number, body: unknown, pathname = "/", search = ""): string | null {
  const signedOut = status === 401 && typeof body === "object" && body !== null && (body as { error?: unknown }).error === "signed_out";
  return signedOut ? signInHref(pathname, search) : null;
}
