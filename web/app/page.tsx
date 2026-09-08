import { ErrorCard } from "@/components/error-card";
import { WeekLive } from "@/components/week-live";
import { apiGet, type Week } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Page() {
  let data: Week;
  try {
    data = await apiGet<Week>("/week");
  } catch (e) {
    return <ErrorCard error={e} />;
  }
  return <WeekLive initial={data} />;
}
