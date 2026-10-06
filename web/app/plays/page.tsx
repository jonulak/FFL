import { ErrorCard } from "@/components/error-card";
import { PlaysLive } from "@/components/plays-live";
import { apiGet, type Plays } from "@/lib/api";
import { errorFields } from "@/lib/errors";
import { parseTeam } from "@/lib/matchup-link";

export const dynamic = "force-dynamic";

// Every play-feed row of the week for one game, newest first: mine, or with `?team=` the game of
// that team (a League board's strip links its left team). The card's own heading line leads back.
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const team = parseTeam((await searchParams).team);
  const path = team == null ? "/plays" : `/plays?team=${team}`;
  let data: Plays;
  try {
    data = await apiGet<Plays>(path);
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return <PlaysLive initial={data} path={path} />;
}
