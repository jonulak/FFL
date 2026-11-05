import type { NextRequest } from "next/server";
import { ApiError, apiGet, apiResponse, type Player, type Side, type Week } from "@/lib/api";
import { markSource, parseShare } from "@/lib/share";
import { clubLogoSrc } from "@/lib/teams";
import { ART_KEEP_S, dataUrl, loadArt, shareImage } from "./image";

const MARK_MS = 2_000;

// A club's mark as a data URL, or null for its abbreviation tile: satori has no session for /api/logo, and a slow or
// missing mark must not cost the image (the share sheet needs the tap still fresh when the image lands).
async function mark(side: Side | null | undefined): Promise<string | null> {
  const source = side && markSource(clubLogoSrc(side));
  if (!source) return null;
  try {
    const r = "api" in source
      ? await apiResponse(source.api, MARK_MS)
      : await fetch(source.cdn, { redirect: "error", cache: "force-cache", next: { revalidate: ART_KEEP_S }, signal: AbortSignal.timeout(MARK_MS) });
    return await dataUrl(r);
  } catch {
    return null;
  }
}

// GET /share?kind=roster|matchup&week=N[&team=T][&teams=0]: the PNG the Share button hands to the share sheet. apiGet's session
// and league make it the visitor's own view. A signed-out read redirects to sign in (apiGet), which the button sees as not an image.
export async function GET(request: NextRequest) {
  const want = parseShare(request.nextUrl.searchParams);
  if (!want) return new Response("bad share request", { status: 400 });
  let data: Week;
  try {
    data = await apiGet<Week>(`/week/${want.week}${want.team ? `?team=${want.team}` : ""}`);
  } catch (e) {
    if (e instanceof ApiError) return new Response(e.message, { status: e.status || 502 });
    throw e;
  }
  const players: Player[] = want.kind === "matchup"
    ? [...data.me.lineup, ...(data.opp?.lineup ?? [])]
    : [...data.me.lineup, ...data.me.bench, ...data.me.ir];
  const [me, opp, art] = await Promise.all([want.teams ? mark(data.me) : null, want.teams && want.kind === "matchup" ? mark(data.opp) : null, loadArt(players)]);
  return shareImage(want.kind, data, { me, opp }, art, Date.now(), want.teams);
}
