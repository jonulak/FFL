import { ErrorCard } from "@/components/error-card";
import { StaleBadge } from "@/components/stale-badge";
import { WatchList } from "@/components/watch-list";
import { apiGet, type WatchPage } from "@/lib/api";
import { errorFields } from "@/lib/errors";

export const dynamic = "force-dynamic";

export default async function Page() {
  let data: WatchPage;
  try {
    data = await apiGet<WatchPage>("/watch");
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
        <h1 className="font-display text-4xl font-bold leading-none tracking-tight">
          Watch
          <span className="ml-2.5 font-semibold text-muted-foreground">Week {data.week}</span>
        </h1>
        <StaleBadge since={data.stale_since} />
        <div className="basis-full max-md:hidden">
          <p className="max-w-[68ch] text-xs leading-4 text-muted-foreground">
            Checked every hour: a hurt teammate ahead of him (Depth), a jump in snaps, targets or red-zone looks (Usage),
            good news (News), his worth to your roster turning positive (Bench). Moving: one lit in the last 3 days, or just dropped (Available).
          </p>
        </div>
      </div>
      <WatchList initial={data} />
    </div>
  );
}
