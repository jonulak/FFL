"use client";

import { ChevronDown } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Toggle } from "@/components/ui/toggle";
import type { Decision, Decisions, Presets } from "@/lib/api";
import { byWeek, KIND_LABEL, needsRationale, reasonText } from "@/lib/decisions";
import { NAME_LINK } from "@/lib/player-log";
import { Markdown } from "./markdown";
import { PlayerTrigger } from "./player-dialog";
import { RationaleDialog } from "./rationale-dialog";
import { SectionHeading } from "./section-heading";

export type Filters = { week: number | null; kind: string | null; open: boolean };

// The filters live in the URL like the Players page's, so a link lands on the same view.
function href(f: Filters): string {
  const q = new URLSearchParams();
  if (f.week != null) q.set("week", String(f.week));
  if (f.kind) q.set("kind", f.kind);
  if (f.open) q.set("open", "1");
  const s = q.toString();
  return s ? `/decisions?${s}` : "/decisions";
}

const ALL = "all";

type Move = { player_id: number; name: string; pos?: string; from?: string; to?: string; call?: string; gap?: number | null };

// "Name: call START, gap +2.5 · BE → FLEX", the lineup move as it was recorded.
function MoveLine({ m }: { m: Move }) {
  const gap = m.gap == null ? null : `${m.gap >= 0 ? "+" : "−"}${Math.abs(m.gap).toFixed(1)}`;
  const seed = { player_id: m.player_id, name: m.name, pos: m.pos ?? "", team: "" };
  return (
    <li>
      <PlayerTrigger p={seed} className={`rounded-sm ${NAME_LINK}`}>{m.name}</PlayerTrigger>
      <span className="text-muted-foreground">
        : call {m.call}{gap && `, gap ${gap}`}{m.from && m.to && ` · ${m.from} → ${m.to}`}
      </span>
    </li>
  );
}

function Labelled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{label}</p>
      <p className="whitespace-pre-line">{children}</p>
    </div>
  );
}

function Row({ d, presets, onEdit }: { d: Decision; presets: Presets; onEdit: () => void }) {
  const reason = reasonText(d, presets);
  const moves = (d.facts?.moves as Move[] | undefined) ?? [];
  return (
    <li className="border-t border-foreground/10 first:border-t-0">
      <Collapsible>
        <CollapsibleTrigger className="group flex w-full items-start gap-3 px-3 py-3 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/60 sm:px-4">
          <Badge variant="muted" shape="chip" className="mt-0.5 shrink-0">{KIND_LABEL[d.kind] ?? d.kind}</Badge>
          <span className="min-w-0 flex-1 space-y-0.5">
            <span className="block font-medium leading-snug">{d.title}</span>
            {needsRationale(d)
              ? <Badge variant="warn">Needs a reason</Badge>
              : reason && <span className="block text-sm text-muted-foreground">{reason}</span>}
            {d.outcome && <span className="block text-sm text-muted-foreground">{d.outcome}</span>}
          </span>
          <time dateTime={d.at} className="shrink-0 text-xs tabular-nums text-muted-foreground">{d.at.slice(0, 10)}</time>
          <ChevronDown aria-hidden className="mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform group-aria-expanded:rotate-180" />
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="space-y-3 px-3 pb-3 text-sm sm:px-4">
            {d.decision && <p className="whitespace-pre-line">{d.decision}</p>}
            {moves.length > 0 && <ul className="space-y-0.5">{moves.map((m) => <MoveLine key={m.player_id} m={m} />)}</ul>}
            {d.facts?.uncertain === true && <p className="text-close-foreground dark:text-close">ESPN did not answer; check the move happened</p>}
            {d.alternatives && <Labelled label="Alternatives">{d.alternatives}</Labelled>}
            {d.exit_conditions && <Labelled label="Exit conditions">{d.exit_conditions}</Labelled>}
            {d.body && <Markdown text={d.body} />}
            <Button variant="outline" size="sm" onClick={onEdit}>Edit</Button>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </li>
  );
}

function FilterSelect({ label, value, shown, options, onChange }: {
  label: string; value: string; shown: string; options: { value: string; label: string }[]; onChange: (v: string) => void;
}) {
  return (
    <Select value={value} onValueChange={(v) => v != null && v !== value && onChange(v)}>
      <SelectTrigger size="sm" aria-label={label} className="font-display text-[0.9375rem] font-semibold uppercase tracking-[0.12em]">
        <SelectValue>{shown}</SelectValue>
      </SelectTrigger>
      <SelectContent alignItemWithTrigger={false}>
        {options.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
      </SelectContent>
    </Select>
  );
}

// The decision journal: every call I made, grouped by week, with the reason beside it or an
// amber "Needs a reason". The row opens to what was weighed; Edit gives the reason and the outcome.
export function DecisionsView({ data, presets, filters }: { data: Decisions; presets: Presets; filters: Filters }) {
  const router = useRouter();
  const [rows, setRows] = useState(data.decisions);
  const [editing, setEditing] = useState<Decision | null>(null);
  // a fresh server render (a filter, a refresh) replaces local edits; React's adjust-state-in-render pattern
  const [seen, setSeen] = useState(data.decisions);
  if (seen !== data.decisions) { setSeen(data.decisions); setRows(data.decisions); }

  const kinds = Object.keys(KIND_LABEL).filter((k) => data.decisions.some((r) => r.kind === k) || filters.kind === k);
  const go = (next: Partial<Filters>) => router.push(href({ ...filters, ...next }), { scroll: false });
  const groups = byWeek(rows);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="font-display text-2xl font-semibold uppercase tracking-[0.14em]">Decisions</h1>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Toggle
            variant="outline"
            pressed={filters.open}
            onPressedChange={(on) => go({ open: on })}
            className="h-10 rounded-md border-foreground/15 px-3 font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-foreground/70 hover:text-foreground sm:h-8 aria-pressed:border-foreground aria-pressed:bg-foreground aria-pressed:text-background aria-pressed:hover:bg-foreground/85"
          >
            Open
          </Toggle>
          <FilterSelect label="Week" value={filters.week == null ? ALL : String(filters.week)} shown={filters.week == null ? "All weeks" : `Week ${filters.week}`}
            options={[{ value: ALL, label: "All weeks" }, ...data.weeks.map((w) => ({ value: String(w), label: `Week ${w}` }))]}
            onChange={(v) => go({ week: v === ALL ? null : Number(v) })} />
          <FilterSelect label="Kind" value={filters.kind ?? ALL} shown={filters.kind ? KIND_LABEL[filters.kind] ?? filters.kind : "All kinds"}
            options={[{ value: ALL, label: "All kinds" }, ...kinds.map((k) => ({ value: k, label: KIND_LABEL[k] }))]}
            onChange={(v) => go({ kind: v === ALL ? null : v })} />
        </div>
      </div>

      {groups.length === 0 && (
        <p className="py-6 text-center text-muted-foreground">
          {filters.open ? "Nothing open" : "No decisions yet"}
          {(filters.open || filters.week != null || filters.kind) && <> · <Link href="/decisions" className="underline">clear filters</Link></>}
        </p>
      )}
      {groups.map((g) => (
        <section key={g.week ?? "season"} className="space-y-1">
          <SectionHeading title={g.week == null ? "Season" : `Week ${g.week}`} count={g.rows.length} />
          <ul>
            {g.rows.map((d) => <Row key={d.id} d={d} presets={presets} onEdit={() => setEditing(d)} />)}
          </ul>
        </section>
      ))}

      <RationaleDialog decision={editing} presets={presets} withOutcome onClose={() => setEditing(null)}
        onSaved={(saved) => setRows((rs) => rs.map((r) => (r.id === saved.id ? saved : r)))} />
    </div>
  );
}
