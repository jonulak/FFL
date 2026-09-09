import { ErrorCard } from "@/components/error-card";
import { LeagueView } from "@/components/league";
import { apiGet, type League } from "@/lib/api";
import { errorFields } from "@/lib/errors";

export const dynamic = "force-dynamic";

// `?week=n` picks the scoreboard week; anything that is not a positive integer means the current one.
function parseWeek(raw: string | string[] | undefined): number | null {
  const n = Number(Array.isArray(raw) ? raw[0] : raw);
  return Number.isInteger(n) && n > 0 ? n : null;
}

export default async function Page({ searchParams }: { searchParams: Promise<{ week?: string | string[] }> }) {
  const week = parseWeek((await searchParams).week);
  let d: League;
  try {
    d = await apiGet<League>(week ? `/league?week=${week}` : "/league");
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return <LeagueView data={d} />;
}
