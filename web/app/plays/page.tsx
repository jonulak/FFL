import { ErrorCard } from "@/components/error-card";
import { PlaysLive } from "@/components/plays-live";
import { apiGet, type Plays } from "@/lib/api";
import { errorFields } from "@/lib/errors";
import { parseTeam, parseWeek } from "@/lib/matchup-link";

export const dynamic = "force-dynamic";

// Every play-feed row of a week for one game, newest first: mine, or with `?team=` the game of that
// team (a League board's strip links its left team); `?week=` the board's week, else the current one.
// The card's own heading line leads back.
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const params = await searchParams;
  const team = parseTeam(params.team);
  const week = parseWeek(params.week);
  const query = new URLSearchParams();
  if (week != null) query.set("week", String(week));
  if (team != null) query.set("team", String(team));
  const path = query.size ? `/plays?${query}` : "/plays";
  let data: Plays;
  try {
    data = await apiGet<Plays>(path);
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return <PlaysLive initial={data} path={path} />;
}
