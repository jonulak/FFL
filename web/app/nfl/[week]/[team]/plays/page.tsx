import { notFound, redirect } from "next/navigation";
import { withLeague } from "@/lib/league";
import { feedHref, parseFilter, parseGameParams } from "@/lib/nfl";

// The full feed used to live here; it is the game page's now. An old link or bookmark lands there, filter and
// league kept.
export default async function Page({ params, searchParams }: {
  params: Promise<{ week: string; team: string }>; searchParams: Promise<{ f?: string | string[]; league?: string }>;
}) {
  const { week, team } = await params;
  const p = parseGameParams(week, team);
  if (!p) notFound();
  const q = await searchParams;
  redirect(withLeague(feedHref(p.week, p.team, parseFilter(q.f)), typeof q.league === "string" ? q.league : null));
}
