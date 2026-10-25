import { DecisionsView } from "@/components/decisions";
import { ErrorCard } from "@/components/error-card";
import Link from "@/components/league-link";
import { apiGet, type DecisionPrefs, type Decisions, type Presets } from "@/lib/api";
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
  let prefs: DecisionPrefs;
  try {
    [data, presets, prefs] = await Promise.all([
      apiGet<Decisions>(`/decisions${q.size ? `?${q}` : ""}`),
      apiGet<Presets>("/decisions/presets"),
      apiGet<DecisionPrefs>("/decisions/prefs"),
    ]);
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  // turned off in Settings: an old link or bookmark lands here, so say where it went
  if (!prefs.page) return (
    <p className="text-sm text-muted-foreground">
      The Decisions page is off. Turn it back on in <Link href="/settings" className="underline underline-offset-2">Settings</Link>.
    </p>
  );
  return <DecisionsView data={data} presets={presets} filters={{ week, kind, open }} />;
}
