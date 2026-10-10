import { DecisionsView } from "@/components/decisions";
import { ErrorCard } from "@/components/error-card";
import { apiGet, type Decisions, type Presets } from "@/lib/api";
import { errorFields } from "@/lib/errors";

export const dynamic = "force-dynamic";

type Params = Promise<Record<string, string | string[] | undefined>>;

const first = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);

// The decision journal; `?week=`, `?kind=` and `?open=1` (only rows still owed a reason) filter it.
export default async function Page({ searchParams }: { searchParams: Params }) {
  const p = await searchParams;
  const w = Number(first(p.week));
  const week = Number.isInteger(w) && w > 0 ? w : null;
  const kind = first(p.kind) || null;
  const open = first(p.open) === "1";
  const q = new URLSearchParams();
  if (week) q.set("week", String(week));
  if (kind) q.set("kind", kind);
  if (open) q.set("open", "1");
  let data: Decisions;
  let presets: Presets;
  try {
    [data, presets] = await Promise.all([
      apiGet<Decisions>(`/decisions${q.size ? `?${q}` : ""}`),
      apiGet<Presets>("/decisions/presets"),
    ]);
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return <DecisionsView data={data} presets={presets} filters={{ week, kind, open }} />;
}
