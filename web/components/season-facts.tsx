import type { Form } from "@/lib/api";
import { formCell, pts, signed, type Trend } from "@/lib/format";
import { TrendArrow } from "./form-strip";
import type { PlayerSeed } from "./player-header";

// One figure: the label in the board's small caps over the number in the condensed face, a
// muted note beneath when the number needs one (the models' spread, a short window). `lead` is
// the row's own number at full ink; the rest step back a shade.
type Stat = { key: string; label: string; value: string | null; note?: string; title?: string; trend?: Trend; lead?: boolean };

function Cell({ s }: { s: Stat }) {
  return (
    <div title={s.title} className="min-w-0">
      <dt className="font-display text-[0.6875rem] font-semibold uppercase leading-none tracking-[0.12em] text-muted-foreground">{s.label}</dt>
      <dd className={`mt-1 font-display text-lg leading-none tabular-nums tracking-tight ${s.lead ? "font-semibold" : "font-medium text-foreground/80"}`}>
        {s.value ?? <span className="text-muted-foreground">—</span>}
        <TrendArrow trend={s.trend} className="ml-0.5" />
      </dd>
      {s.note && <dd className="mt-0.5 text-[0.6875rem] leading-tight tabular-nums text-muted-foreground">{s.note}</dd>}
    </div>
  );
}

// One row of the block: its name down the left, its figures on the shared four-column grid, so
// a column reads straight down through the rows. A row with nothing to show is left out.
function Row({ name, stats }: { name: string; stats: Stat[] }) {
  if (stats.length === 0) return null;
  return (
    <div className="grid grid-cols-[3.75rem_minmax(0,1fr)] items-start gap-x-2 py-2.5">
      <p className="pt-px font-display text-xs font-semibold uppercase leading-none tracking-[0.14em] text-foreground/85">{name}</p>
      <dl className="grid grid-cols-4 gap-x-2 gap-y-3">
        {stats.map((s) => <Cell key={s.key} s={s} />)}
      </dl>
    </div>
  );
}

// A form figure as a Stat: formCell's number, trend and note, under the block's own label.
function fromForm(f: Form, key: keyof Form, label: string): Stat | null {
  const c = formCell(f, key);
  return c ? { key, label, value: c.value, note: c.note, title: c.title, trend: c.trend } : null;
}

// The figures the Players table leaves to the dialog (its Form section), when the row that
// opened it carried them, in three rows: the rest of season (the adjusted ROS add runs on, with the window's weight in it;
// the models' own number with their spread; the two deltas on a free agent), his last three games
// against the projection, and his usage over them.
export function SeasonFacts({ p }: { p: PlayerSeed & { ros_adj: number } }) {
  const f = p.form;
  const ros: Stat[] = [{
    key: "ros", label: "adjusted", value: pts(p.ros_adj), lead: true,
    note: f ? `wt ${f.weight.toFixed(1)} of ${f.games}` : undefined,
    title: "usage-adjusted rest of season: the models' rate shrunk toward his last games by their snap-weighted count (wt; the prior is worth 4); what add runs on",
  }];
  if (p.ros_proj != null) {
    const src = [p.ros_sleeper != null && `Sleeper ${pts(p.ros_sleeper)}`, p.ros_cbs != null && `CBS ${pts(p.ros_cbs)}`].filter(Boolean).join(" · ");
    ros.push({
      key: "model", label: "model", value: pts(p.ros_proj),
      note: p.ros_spread != null ? `±${pts(p.ros_spread)}` : src ? "1 source" : "season",
      title: src ? `the models' rest of season, averaged: ${src}` : "no rest-of-season source has him: season projection scaled by the weeks left",
    });
  }
  if (p.delta_roster != null) ros.push({ key: "dr", label: "Δ roster", value: signed(p.delta_roster), title: "ROS against my weakest at the position" });
  if (p.delta_pool != null) ros.push({ key: "dp", label: "Δ pool", value: signed(p.delta_pool), title: "ROS against the league's replacement level" });
  const pick = (pairs: [keyof Form, string][]) => f ? pairs.flatMap(([k, l]) => fromForm(f, k, l) ?? []) : [];
  const last = pick([["l3", "pts/g"], ["beat", "beat"], ["pace_gap", "pace"]]);
  const usage = pick([["opp", "opp"], ["snap_pct", "snap"], ["tgt_snap", "tgt/snap"], ["ypt", "ypt"]]);
  return (
    <div className="divide-y divide-foreground/10">
      <Row name="ROS" stats={ros} />
      <Row name="Last 3" stats={last} />
      <Row name="Usage" stats={usage} />
    </div>
  );
}
