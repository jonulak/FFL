import { notFound } from "next/navigation";
import { ErrorCard } from "@/components/error-card";
import { MatchupLive } from "@/components/matchup-live";
import { apiGet, type Week } from "@/lib/api";
import { errorFields } from "@/lib/errors";
import { parseMatchupParams } from "@/lib/matchup-link";

export const dynamic = "force-dynamic";

// Any game on the League scoreboard, on the Matchup page's board: `/league/<week>/<team>`, the
// team being either side of it (the tile links the away team).
export default async function Page({ params }: { params: Promise<{ week: string; team: string }> }) {
  const { week: w, team: t } = await params;
  const at = parseMatchupParams(w, t);
  if (!at) notFound();
  const path = `/week/${at.week}?team=${at.team}`;
  let data: Week;
  try {
    data = await apiGet<Week>(path);
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return <MatchupLive initial={data} path={path} back={`/league?week=${at.week}`} />;
}
