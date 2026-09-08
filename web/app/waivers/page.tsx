import { ErrorCard } from "@/components/error-card";
import { PosTabs } from "@/components/pos-tabs";
import { StaleBadge } from "@/components/stale-badge";
import { WaiverTable } from "@/components/waiver-table";
import { apiGet, type Waivers } from "@/lib/api";
import { errorFields } from "@/lib/errors";

export const dynamic = "force-dynamic";

export default async function Page({ searchParams }: { searchParams: Promise<{ pos?: string }> }) {
  const { pos = "RB" } = await searchParams;
  let data: Waivers;
  try {
    data = await apiGet<Waivers>(`/waivers?pos=${encodeURIComponent(pos)}`);
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-xl font-semibold">Waivers · week {data.week}</h1>
        <StaleBadge since={data.stale_since} />
      </div>
      <PosTabs active={pos} base="/waivers" />
      <p className="text-xs text-muted-foreground">
        add = projected points gained this season (STRATEGY.md, Waivers). Δ roster = ROS vs my
        weakest at the position; Δ pool = ROS vs league replacement.
      </p>
      <WaiverTable rows={data.rows} week={data.week} />
    </div>
  );
}
