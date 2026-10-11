import Link from "next/link";
import { notFound } from "next/navigation";
import { ErrorCard } from "@/components/error-card";
import { NflGameLive } from "@/components/nfl-game";
import { apiGet, type NflGamePage } from "@/lib/api";
import { errorFields } from "@/lib/errors";
import { parseGameParams } from "@/lib/nfl";

export const dynamic = "force-dynamic";

// One NFL game by week and either club: its scorebug, my and my opponent's starters in it, its latest scoring plays.
export default async function Page({ params }: { params: Promise<{ week: string; team: string }> }) {
  const { week, team } = await params;
  const p = parseGameParams(week, team);
  if (!p) notFound();
  const path = `/nfl/${p.week}/${p.team}`;
  let d: NflGamePage;
  try {
    d = await apiGet<NflGamePage>(path);
  } catch (e) {
    return (
      <div className="space-y-3">
        <ErrorCard {...errorFields(e)} />
        <Link href="/nfl" className="block text-sm text-muted-foreground underline hover:text-foreground">All games</Link>
      </div>
    );
  }
  return <NflGameLive initial={d} path={path} />;
}
