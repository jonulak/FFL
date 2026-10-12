import { notFound, redirect } from "next/navigation";
import { feedHref, parseFilter, parseGameParams } from "@/lib/nfl";

// The full feed used to live here; it is the game page's now. An old link or bookmark lands there, filter kept.
export default async function Page({ params, searchParams }: {
  params: Promise<{ week: string; team: string }>; searchParams: Promise<{ f?: string | string[] }>;
}) {
  const { week, team } = await params;
  const p = parseGameParams(week, team);
  if (!p) notFound();
  redirect(feedHref(p.week, p.team, parseFilter((await searchParams).f)));
}
