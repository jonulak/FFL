import { ErrorCard } from "@/components/error-card";
import { NflBoardLive } from "@/components/nfl-board";
import { apiGet, type NflBoard } from "@/lib/api";
import { errorFields } from "@/lib/errors";

export const dynamic = "force-dynamic";

function parseWeek(raw: string | string[] | undefined): number | null {
  const n = Number(Array.isArray(raw) ? raw[0] : raw);
  return Number.isInteger(n) && n > 0 ? n : null;
}

// Every NFL game of a week (`?week=n`, else ESPN's current one): what is on first, then by kickoff window.
export default async function Page({ searchParams }: { searchParams: Promise<{ week?: string | string[] }> }) {
  const week = parseWeek((await searchParams).week);
  const path = week ? `/nfl?week=${week}` : "/nfl";
  let d: NflBoard;
  try {
    d = await apiGet<NflBoard>(path);
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return <NflBoardLive initial={d} path={path} />;
}
