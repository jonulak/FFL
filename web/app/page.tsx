import { ErrorCard } from "@/components/error-card";
import { WeekLive } from "@/components/week-live";
import { ApiError, apiGet, type Week } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Page() {
  let data: Week;
  try {
    data = await apiGet<Week>("/week");
  } catch (e) {
    const props =
      e instanceof ApiError
        ? { status: e.status, source: e.source, message: e.message }
        : { status: 0, source: "app", message: String(e) };
    return <ErrorCard {...props} />;
  }
  return <WeekLive initial={data} />;
}
