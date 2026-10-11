import Link from "next/link";
import { notFound } from "next/navigation";
import { ErrorCard } from "@/components/error-card";
import { NflFeedLive } from "@/components/nfl-feed";
import { apiGet, type NflGamePage } from "@/lib/api";
import { errorFields } from "@/lib/errors";
import { gameHref, parseFilter, parseGameParams } from "@/lib/nfl";

export const dynamic = "force-dynamic";

// Every play of one NFL game, newest first under its drives; `?f=mine|scoring` filters (URL state, so back and reload keep it).
export default async function Page({ params, searchParams }: {
  params: Promise<{ week: string; team: string }>; searchParams: Promise<{ f?: string | string[] }>;
}) {
  const { week, team } = await params;
  const p = parseGameParams(week, team);
  if (!p) notFound();
  const filter = parseFilter((await searchParams).f);
  const path = `/nfl/${p.week}/${p.team}`;
  let d: NflGamePage;
  try {
    d = await apiGet<NflGamePage>(path);
  } catch (e) {
    return (
      <div className="space-y-3">
        <ErrorCard {...errorFields(e)} />
        <Link href={gameHref(p.week, p.team)} className="block text-sm text-muted-foreground underline hover:text-foreground">Back to the game</Link>
        <Link href="/nfl" className="block text-sm text-muted-foreground underline hover:text-foreground">All games</Link>
      </div>
    );
  }
  return <NflFeedLive initial={d} path={path} filter={filter} club={p.team} />;
}
