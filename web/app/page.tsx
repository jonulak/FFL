import { ErrorCard } from "@/components/error-card";
import { MatchupLive } from "@/components/matchup-live";
import { apiGet, type Week } from "@/lib/api";
import { errorFields } from "@/lib/errors";

export const dynamic = "force-dynamic";

export default async function Page() {
  let data: Week;
  try {
    data = await apiGet<Week>("/week");
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return <MatchupLive initial={data} />;
}
