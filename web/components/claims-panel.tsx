"use client";

import {
  DndContext, KeyboardSensor, MouseSensor, TouchSensor, closestCenter, useSensor, useSensors, type DragEndEvent,
} from "@dnd-kit/core";
import { SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Check, ChevronUp, GripVertical, LoaderCircle, Plus, Trash2, X } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Combobox, ComboboxContent, ComboboxEmpty, ComboboxInput, ComboboxItem, ComboboxList } from "@/components/ui/combobox";
import { Label } from "@/components/ui/label";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import type { ClaimGroup, ClaimPlayer } from "@/lib/api";
import {
  addCut, capacity, cutBlock, displayTargets, groupHasFA, groupPos, move, moveGroup, newGroup, processDay, removeFrom,
  removeGroup, reorderTargets, setOpen, statusTag,
} from "@/lib/claims";
import { pts, signed } from "@/lib/format";
import { useClaims } from "./claims-context";
import { ClaimsSubmit } from "./claims-submit";
import { Clock } from "./local-time";

const LABEL = "font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground";
const SUB = "font-display text-[0.6875rem] font-semibold uppercase tracking-[0.14em] text-foreground/55";
const ROW = "grid grid-cols-[1.5rem_1.25rem_minmax(0,1fr)_auto_1.75rem] items-center gap-x-1.5 border-t border-foreground/8 py-1 pointer-coarse:py-2.5";
// On a touch screen the 24px grips and remove buttons keep their look but take a 44px hit area
// (an ::after 10px out on every side); the rows grow to 44px there so neighbours never overlap.
const HIT = "relative pointer-coarse:after:absolute pointer-coarse:after:-inset-2.5";

// Mouse drags start after 4px of travel; a touch drag after a 150 ms hold, so a swipe on the
// sheet still scrolls it. The keyboard moves a focused handle with the arrows (space to lift).
function useDragSensors() {
  return useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 4 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 150, tolerance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
}

// The drag handle every sortable row and card carries; only the handle starts a drag.
type Sortable = ReturnType<typeof useSortable>;
function Grip({ label, handleRef, attributes, listeners, disabled }: {
  label: string; handleRef: Sortable["setActivatorNodeRef"]; attributes: Sortable["attributes"]; listeners: Sortable["listeners"]; disabled: boolean;
}) {
  return (
    <button type="button" ref={handleRef} {...attributes} {...listeners} disabled={disabled} aria-label={label}
      className={`${HIT} flex size-6 cursor-grab touch-none items-center justify-center rounded-sm text-foreground/40 hover:text-foreground focus-visible:outline-2 active:cursor-grabbing disabled:cursor-default disabled:opacity-40`}>
      <GripVertical aria-hidden className="size-4" />
    </button>
  );
}

function Remove({ label, onClick, disabled }: { label: string; onClick: () => void; disabled: boolean }) {
  return (
    <Button variant="ghost" size="icon-xs" aria-label={label} onClick={onClick} disabled={disabled} className={`${HIT} text-foreground/50 hover:text-foreground`}>
      <X aria-hidden />
    </Button>
  );
}

// One sortable line: grip, rank, name with its tags, the number, remove.
function Line({ id, rank, name, tags, value, onRemove, removeLabel, disabled, tone = "" }: {
  id: string; rank: ReactNode; name: string; tags?: ReactNode; value: ReactNode; onRemove: () => void; removeLabel: string; disabled: boolean; tone?: string;
}) {
  const { setNodeRef, setActivatorNodeRef, attributes, listeners, transform, transition, isDragging } = useSortable({ id, disabled });
  return (
    <li ref={setNodeRef} style={{ transform: CSS.Translate.toString(transform), transition }}
      className={`${ROW} ${isDragging ? "relative z-10 rounded-md bg-card shadow-[0_8px_20px_-10px_rgb(0_0_0_/_0.45)]" : ""}`}>
      <Grip label={`Move ${name}`} handleRef={setActivatorNodeRef} attributes={attributes} listeners={listeners} disabled={disabled} />
      <span className="text-right font-display text-sm font-semibold tabular-nums text-muted-foreground">{rank}</span>
      <span className="flex min-w-0 items-center gap-1.5">
        <span className={`truncate font-medium ${tone}`}>{name}</span>
        {tags}
      </span>
      <span className="text-right text-xs tabular-nums text-foreground/70">{value}</span>
      <Remove label={removeLabel} onClick={onRemove} disabled={disabled} />
    </li>
  );
}

// The roster picker under "make room": weakest first by adjusted ROS, every player listed; one
// who cannot go (undroppable, on IR, his game started in a group that adds a free agent now) is
// shown disabled with the reason, so his absence from the cuts is explained.
function CutPicker({ gi, roster, used, hasFA, disabled }: { gi: number; roster: [number, ClaimPlayer][]; used: Set<number>; hasFA: boolean; disabled: boolean }) {
  const { groups, setGroups } = useClaims();
  const items = roster.filter(([id]) => !used.has(id)).map(([id, p]) => ({ id, p, block: cutBlock(p, hasFA) }));
  type Item = (typeof items)[number];
  return (
    <Combobox<Item> items={items} value={null} disabled={disabled}
      onValueChange={(v) => { if (v && !v.block) setGroups(addCut(groups, gi, v.id)); }}
      itemToStringLabel={(i) => i.p.name} isItemEqualToValue={(a, b) => a.id === b.id}>
      <ComboboxInput placeholder="Cut from roster…" aria-label={`Add a cut to group ${gi + 1}`} className="mt-2 h-8 w-full" />
      <ComboboxContent>
        <ComboboxEmpty>Nobody left on the roster by that name.</ComboboxEmpty>
        <ComboboxList>
          {(i: Item) => (
            <ComboboxItem key={i.id} value={i} disabled={!!i.block}>
              <span className="min-w-0 flex-1 truncate">{i.p.name}</span>
              <span className="text-xs tabular-nums text-muted-foreground">
                {i.block ?? `${i.p.pos} · ROS ${pts(i.p.ros_adj)}`}
              </span>
            </ComboboxItem>
          )}
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  );
}

// One group: "get K of N", the targets best first (free agents on top: they are added the moment
// you submit), then the rows that make room, first to go first. Its grip reorders the groups.
function GroupCard({ g, gi, roster, usedCuts }: { g: ClaimGroup; gi: number; roster: [number, ClaimPlayer][]; usedCuts: Set<number> }) {
  const { groups, setGroups, pos, status, player, data, dirty, submitting } = useClaims();
  const { setNodeRef, setActivatorNodeRef, attributes, listeners, transform, transition, isDragging } = useSortable({ id: `g${gi}`, disabled: submitting });
  const sensors = useDragSensors();
  const dndId = useId();
  const shown = displayTargets(g, status);
  const fa = shown.filter((t) => t.now);
  const waiting = shown.filter((t) => !t.now);
  const hasFA = groupHasFA(g, status);
  const errors = dirty ? [] : data.errors.filter((e) => e.group === gi);
  const name = (id: number) => player(id)?.name ?? `player ${id}`;
  const p = groupPos(g, pos);

  const onDragEnd = ({ active, over }: DragEndEvent) => {
    const from = active.data.current?.sortable as { containerId: string; index: number } | undefined;
    const to = over?.data.current?.sortable as { containerId: string; index: number } | undefined;
    if (!from || !to || from.containerId !== to.containerId || from.index === to.index) return;
    if (from.containerId.endsWith("cuts")) setGroups(move(groups, gi, "cuts", from.index, to.index));
    else {
      const next = groups.map((x) => ({ ...x }));
      next[gi] = { ...g, targets: reorderTargets(g, status, from.containerId.endsWith("fa"), from.index, to.index) };
      setGroups(next);
    }
  };

  const targetLine = (t: { id: number; now: boolean }, rank: number) => {
    const pl = player(t.id);
    const tag = statusTag(pl);
    return (
      <Line key={t.id} id={`t${t.id}`} rank={rank} name={name(t.id)} disabled={submitting}
        tags={t.now
          ? <Badge variant="default" shape="chip" className="h-4 px-1 text-[0.6875rem]" title="a free agent: added the moment you submit, ahead of the claims">now</Badge>
          : tag && <span className="font-display text-[0.6875rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{tag}</span>}
        value={pl?.add != null ? <>add <span className="font-display text-sm font-semibold text-foreground">{signed(pl.add)}</span></> : null}
        onRemove={() => setGroups(removeFrom(groups, gi, "targets", t.id))} removeLabel={`Remove ${name(t.id)} from group ${gi + 1}`} />
    );
  };

  return (
    <li ref={setNodeRef} style={{ transform: CSS.Translate.toString(transform), transition }}
      className={isDragging ? "relative z-10" : ""}>
      <Card size="sm" className={`gap-0 rounded-lg px-3 py-2.5 ${isDragging ? "shadow-[0_12px_28px_-12px_rgb(0_0_0_/_0.5)]" : "shadow-[0_6px_18px_-14px_rgb(0_0_0_/_0.45)]"}`}>
        <div className="flex items-center gap-1.5">
          <Grip label={`Move group ${gi + 1}`} handleRef={setActivatorNodeRef} attributes={attributes} listeners={listeners} disabled={submitting} />
          <h3 className="font-display text-xl font-bold leading-none tracking-tight">
            {gi + 1}{p && <span className="text-foreground/80"> · {p}</span>}
          </h3>
          {g.targets.length > 0 && (
            <Badge variant="close" shape="chip" className="ml-auto">get {Math.min(capacity(g), g.targets.length)} of {g.targets.length}</Badge>
          )}
          <Button variant="ghost" size="icon-xs" aria-label={`Delete group ${gi + 1}`} disabled={submitting}
            onClick={() => setGroups(removeGroup(groups, gi))} className={`${HIT} text-foreground/45 hover:text-destructive ${g.targets.length ? "" : "ml-auto"}`}>
            <Trash2 aria-hidden />
          </Button>
        </div>

        <DndContext id={dndId} sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
          <p className={`${SUB} mt-3 mb-0.5`}>targets · best first</p>
          {g.targets.length === 0 && (
            <p className="py-1.5 text-xs text-muted-foreground">Pick targets with the claim button on any row of the table.</p>
          )}
          <SortableContext id={`${dndId}fa`} items={fa.map((t) => `t${t.id}`)} strategy={verticalListSortingStrategy}>
            <ol>{fa.map((t, i) => targetLine(t, i + 1))}</ol>
          </SortableContext>
          <SortableContext id={`${dndId}w`} items={waiting.map((t) => `t${t.id}`)} strategy={verticalListSortingStrategy}>
            <ol>{waiting.map((t, i) => targetLine(t, fa.length + i + 1))}</ol>
          </SortableContext>

          <p className={`${SUB} mt-3 mb-0.5`}>make room · first to go first</p>
          <ol>
            {g.open > 0 && (
              <li className={ROW}>
                <span />
                <span className="text-right font-display text-sm font-semibold tabular-nums text-muted-foreground">{g.open > 1 ? `1–${g.open}` : 1}</span>
                <span className="text-foreground/70 italic">open roster spot{g.open > 1 && ` ×${g.open}`}</span>
                <span />
                <span />
              </li>
            )}
          </ol>
          <SortableContext id={`${dndId}cuts`} items={g.cuts.map((c) => `c${c}`)} strategy={verticalListSortingStrategy}>
            <ol>
              {g.cuts.map((c, i) => {
                const pl = player(c);
                const block = pl ? cutBlock(pl, hasFA) : null;
                return (
                  <Line key={c} id={`c${c}`} rank={g.open + i + 1} name={name(c)} disabled={submitting} tone="text-out"
                    tags={block && <span className="text-xs text-destructive">{block}</span>}
                    value={pl?.ros_adj != null ? `ROS ${pts(pl.ros_adj)}` : null}
                    onRemove={() => setGroups(removeFrom(groups, gi, "cuts", c))} removeLabel={`Remove ${name(c)} from the cuts`} />
                );
              })}
            </ol>
          </SortableContext>
        </DndContext>
        {capacity(g) === 0 && g.targets.length > 0 && (
          <p className="py-1 text-xs text-muted-foreground">Add a cut: the group gets one target per row here.</p>
        )}
        <CutPicker gi={gi} roster={roster} used={usedCuts} hasFA={hasFA} disabled={submitting} />
        {data.open_spots > 0 && (
          <Label className="mt-2.5 text-xs font-normal text-foreground/80">
            <Checkbox checked={g.open > 0} disabled={submitting}
              onCheckedChange={(on) => setGroups(setOpen(groups, gi, on, data.open_spots))} />
            use the {data.open_spots} open roster spot{data.open_spots > 1 && "s"}
          </Label>
        )}
        {errors.length > 0 && (
          <ul className="mt-2 space-y-0.5 text-xs text-destructive" aria-live="polite">
            {errors.map((e, i) => <li key={i}>{e.player_id != null && <span className="font-medium">{name(e.player_id)}: </span>}{e.reason}</li>)}
          </ul>
        )}
      </Card>
    </li>
  );
}

// Where the plan stands against ESPN: its claim list unreadable, never sent, changed since the
// last send, ESPN's list no longer the plan's claims (on_espn: checked against ESPN, not the record), or on ESPN.
function SubmitState() {
  const { data } = useClaims();
  if (data.pending_error) return <Badge variant="destructive" className="max-w-full truncate" title={data.pending_error}>ESPN: {data.pending_error}</Badge>;
  if (!data.last_submit) return <Badge variant="muted">not sent</Badge>;
  if (data.edited_since_submit) return <Badge variant="warn">edited since submit</Badge>;
  if (!data.on_espn) return <Badge variant="warn">differs from ESPN</Badge>;
  return <Badge variant="muted"><Check aria-hidden className="stroke-[2.5]" />on ESPN · sent <Clock iso={data.last_submit.at} /></Badge>;
}

// The panel's body, shared by the docked column and the phone's sheet. `heading` is the title
// element each host needs (an h2, or the sheet's labelled title).
function PanelBody({ heading, onReview }: { heading: (text: string) => ReactNode; onReview: () => void }) {
  const { data, groups, setGroups, player, status, dirty, saving, error, submitting } = useClaims();
  const sensors = useDragSensors();
  const dndId = useId();
  const roster = Object.entries(data.players)
    .filter(([, p]) => p.droppable != null) // roster players only: a free agent's slot reads "FA", his droppable null
    .map(([k, p]) => [Number(k), p] as [number, ClaimPlayer])
    .sort((a, b) => (a[1].ros_adj ?? 0) - (b[1].ros_adj ?? 0));
  const usedCuts = new Set(groups.flatMap((g) => g.cuts));
  const day = processDay(groups.flatMap((g) => g.targets).filter((t) => status[String(t)] !== "FREEAGENT").map((t) => player(t)?.waiver_date ?? null));
  const moves = data.now.length + data.claims.length;
  const blocked = dirty || saving || submitting || data.errors.length > 0 || moves === 0;

  const onDragEnd = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id) return;
    setGroups(moveGroup(groups, Number(String(active.id).slice(1)), Number(String(over.id).slice(1))));
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2 max-lg:pr-9">
        {heading(`Claims · process ${day}`)}
        <SubmitState />
      </div>
      {(error || saving) && (
        <p className={`flex items-center gap-1.5 text-xs ${error && !error.startsWith("saving") ? "text-destructive" : "text-muted-foreground"}`} aria-live="polite">
          {saving && <LoaderCircle aria-hidden className="size-3 animate-spin" />}
          {error ?? "saving…"}
        </p>
      )}
      {groups.length === 0 && (
        <p className="max-w-[40ch] text-sm leading-relaxed text-muted-foreground">
          No claims this week. Press <span className="font-medium text-foreground">claim</span> on a player&apos;s row to start a group:
          list the players you&apos;d take, best first, then the roster spots they can fill.
        </p>
      )}
      <DndContext id={dndId} sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
        <SortableContext items={groups.map((_, i) => `g${i}`)} strategy={verticalListSortingStrategy}>
          <ol className="space-y-2.5">
            {groups.map((g, i) => <GroupCard key={i} g={g} gi={i} roster={roster} usedCuts={usedCuts} />)}
          </ol>
        </SortableContext>
      </DndContext>
      <div className="flex flex-wrap items-center gap-2 pt-1">
        <Button variant="ghost" size="sm" disabled={submitting} onClick={() => setGroups(newGroup(groups))}>
          <Plus aria-hidden />New group
        </Button>
        {groups.length > 0 && (
          <Button className="ml-auto" disabled={blocked} onClick={onReview}>
            Review {moves} move{moves === 1 ? "" : "s"}
          </Button>
        )}
      </div>
    </div>
  );
}

// The week's claim groups: a column docked beside the table from `lg`, a bottom sheet behind a
// fixed bar below it. Both open the one Submit dialog.
export function ClaimsPanel() {
  const { data, groups } = useClaims();
  const [sheet, setSheet] = useState(false);
  const [review, setReview] = useState(false);
  const openReview = () => { setSheet(false); setReview(true); };
  const n = data.claims.length + data.now.length;
  return (
    <>
      <aside aria-label="Claims" className="hidden lg:sticky lg:top-16 lg:block lg:max-h-[calc(100dvh-5rem)] lg:overflow-y-auto lg:overscroll-contain lg:pb-4">
        <PanelBody heading={(t) => <h2 className={LABEL}>{t}</h2>} onReview={openReview} />
      </aside>
      <div className="fixed inset-x-0 bottom-0 z-20 border-t border-foreground/10 bg-card/95 px-4 pt-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] shadow-[0_-8px_24px_-16px_rgb(0_0_0_/_0.5)] lg:hidden">
        <Button variant="ghost" className="h-10 w-full justify-between px-2" onClick={() => setSheet(true)}>
          <span className="font-display text-base font-semibold uppercase tracking-[0.1em]">
            Claims
            <span className="text-muted-foreground"> · {groups.length ? `${groups.length} group${groups.length > 1 ? "s" : ""} · ${n} move${n === 1 ? "" : "s"}` : "none yet"}</span>
          </span>
          <ChevronUp aria-hidden className="size-5" />
        </Button>
      </div>
      <Sheet open={sheet} onOpenChange={setSheet}>
        <SheetContent side="bottom" className="max-h-[85dvh] gap-0 overflow-y-auto overscroll-contain rounded-t-xl px-4 pt-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
          <SheetDescription className="sr-only">The week&apos;s claim groups: drag the handles to reorder, then review and send.</SheetDescription>
          <PanelBody heading={(t) => <SheetTitle className={LABEL}>{t}</SheetTitle>} onReview={openReview} />
        </SheetContent>
      </Sheet>
      <ClaimsSubmit open={review} onOpenChange={setReview} />
    </>
  );
}
