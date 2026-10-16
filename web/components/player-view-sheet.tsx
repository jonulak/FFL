"use client";

import { DndContext, KeyboardSensor, MouseSensor, TouchSensor, closestCenter, useSensor, useSensors, type DragEndEvent } from "@dnd-kit/core";
import { SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical, Plus, Trash2, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { clientPost, ClientError } from "@/app/api-client";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Combobox, ComboboxChip, ComboboxChips, ComboboxChipsInput, ComboboxContent, ComboboxEmpty, ComboboxItem, ComboboxList, ComboboxValue, useComboboxAnchor } from "@/components/ui/combobox";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Toggle } from "@/components/ui/toggle";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { Preset, PresetStore, ViewFilter } from "@/lib/api";
import { LABEL } from "@/lib/board";
import { columnSpec, PLAYER_COLUMNS, sortFor } from "@/lib/player-columns";
import { FILTER_SETS } from "@/lib/player-view";
import { TEAMS } from "@/lib/teams";
import { columnOrder, finishedFilters, needsRostered, moveColumn, presetView, removeFilter, setFilter, toggleColumn } from "@/lib/view-draft";
import { PHONE_INSET } from "./claims-submit";
import { usePlayerView } from "./player-view-context";
import { useDesk } from "./swap-sheet";

// The View sheet (ffl-8iz): the table's columns (pick and drag) and filters, edited on a draft and laid
// on with Apply; under them the saved presets, which save as they are changed. A bottom sheet on the
// phone, a right sheet from 48rem.
export type SheetFocus = "view" | "presets";

const PERCENT = new Set(["snap_pct", "air_share"]);
const SET_NAMES: Record<string, string> = { injury: "Injury", avail: "Availability", team: "Team" };
const fieldName = (field: string) => SET_NAMES[field] ?? columnSpec(field)?.label ?? field;
const CLUBS = Object.keys(TEAMS).sort();
// the Players page's chrome: outline controls 40px on a phone, 32px from `sm`, pressed = inverted
const PRESSABLE = "h-10 min-w-10 rounded-md border-foreground/15 px-2.5 text-foreground/70 hover:text-foreground sm:h-8 sm:min-w-8 aria-pressed:border-foreground aria-pressed:bg-foreground aria-pressed:text-background aria-pressed:hover:bg-foreground/85";
const ICON_BTN = "size-10 text-muted-foreground hover:text-foreground sm:size-8";

export function PlayerViewSheet({ open, onOpenChange, focus }: { open: boolean; onOpenChange: (o: boolean) => void; focus: SheetFocus }) {
  const desk = useDesk();
  const body = useRef<HTMLDivElement | null>(null);
  return (
    <Sheet open={open} onOpenChange={onOpenChange} swipeDirection={desk ? "right" : "down"}>
      {/* Base UI's popup stops the arrow keys from bubbling (its composite-widget guard), and dnd-kit's
          keyboard drag reads them off the document: let them through from a drag handle */}
      <SheetContent side={desk ? "right" : "bottom"} initialFocus={body}
        onKeyDown={(e) => { if ((e.target as HTMLElement).closest("[data-drag-handle]")) e.preventBaseUIHandler(); }}
        className={`gap-0 p-0 ${desk ? "data-[side=right]:w-full data-[side=right]:sm:max-w-md" : "max-h-[85dvh] rounded-t-xl"}`}>
        {/* mounted only while open, so each opening starts its draft from the view on the page */}
        <ViewEditor focus={focus} bodyRef={body} phone={!desk} onDone={() => onOpenChange(false)} />
      </SheetContent>
    </Sheet>
  );
}

function ViewEditor({ focus, bodyRef, phone, onDone }: {
  focus: SheetFocus; bodyRef: React.RefObject<HTMLDivElement | null>; phone: boolean; onDone: () => void;
}) {
  const { view, go, pending } = usePlayerView();
  const [cols, setCols] = useState(view.cols);
  const [filters, setFilters] = useState<ViewFilter[]>(view.filters);
  const presets = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (focus === "presets") presets.current?.scrollIntoView({ block: "start" });
  }, [focus]);
  const apply = () => {
    const done = finishedFilters(filters);
    // theirs/mine live in the rostered half: the Free agents toggle must say so
    go({ ...view, cols, filters: done, sort: sortFor(cols, view.sort), ...(needsRostered(done) ? { fa: false } : {}) });
    onDone();
  };
  return (
    <>
      <div className="flex items-baseline gap-3 border-b border-foreground/10 px-4 pt-5 pb-3 pr-14">
        <SheetTitle className="font-display text-2xl leading-none font-bold tracking-tight">View</SheetTitle>
        <SheetDescription className="text-xs text-muted-foreground">Columns and filters apply together; presets save at once.</SheetDescription>
      </div>
      <div ref={bodyRef} tabIndex={-1} className="min-h-0 flex-1 space-y-8 overflow-y-auto overscroll-contain [overflow-anchor:none] px-4 pt-4 pb-6 outline-none">
        <FiltersSection filters={filters} setFilters={setFilters} />
        <ColumnsSection cols={cols} setCols={setCols} scrollRef={bodyRef} />
        <section ref={presets} className="scroll-mt-4">
          <PresetsSection onDone={onDone} />
        </section>
      </div>
      {/* PHONE_INSET, written out: the safe-area inset reads 0 on his phone, so the floor is real padding */}
      <div className={`flex shrink-0 justify-end gap-2 border-t border-foreground/10 bg-popover px-4 pt-3 ${phone ? `gap-3 ${PHONE_INSET}` : "pb-4"}`}>
        <Button variant="outline" onClick={onDone} className={`border-foreground/15 ${phone ? "h-12 flex-1 text-base" : "h-9 px-4"}`}>Cancel</Button>
        <Button onClick={apply} disabled={pending} className={`min-w-24 ${phone ? "h-12 flex-1 text-base" : "h-9 px-4"}`}>Apply</Button>
      </div>
    </>
  );
}

// ---- Columns ---------------------------------------------------------------------------------------

// Sensors as in the claims panel: a mouse drag after 4px, a touch drag after a 150 ms hold (so a swipe on
// the sheet still scrolls it), the keyboard on a focused handle (space lifts, arrows move, space drops).
function useDragSensors() {
  return useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 4 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 150, tolerance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
}

// A drag near the sheet's top or bottom scrolls it. dnd-kit's default starts a fifth of the box from
// the edge at 10x speed, which on the phone's short sheet ran the list away under the finger: start
// in the last tenth only, at a third of the speed.
const AUTO_SCROLL = { threshold: { x: 0, y: 0.1 }, acceleration: 3 };

function ColumnsSection({ cols, setCols, scrollRef }: {
  cols: string[]; setCols: (c: string[]) => void; scrollRef: React.RefObject<HTMLDivElement | null>;
}) {
  const sensors = useDragSensors();
  const dndId = useId();
  // A tick moves the row (to the end of the shown ones, or back to its catalogue place), and the
  // browser scrolled the sheet after the focused box. The sheet holds still: its scroll is put back
  // as soon as the list has moved, and the sheet anchors nothing ([overflow-anchor:none]).
  const hold = useRef<number | null>(null);
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el && hold.current !== null) el.scrollTop = hold.current;
    hold.current = null;
  }, [cols, scrollRef]);
  const toggle = (id: string) => {
    hold.current = scrollRef.current?.scrollTop ?? null;
    setCols(toggleColumn(cols, id));
  };
  const list = columnOrder(cols);
  const ids = list.map((c) => c.id);
  const onDragEnd = ({ active, over }: DragEndEvent) => {
    if (over && active.id !== over.id) setCols(moveColumn(cols, ids, String(active.id), String(over.id)));
  };
  return (
    <section aria-labelledby="view-columns">
      <div className="mb-1.5 flex items-baseline justify-between gap-3">
        <h3 id="view-columns" className={LABEL}>Columns</h3>
        <span className="text-xs text-muted-foreground tabular-nums">{cols.length} shown</span>
      </div>
      <DndContext id={dndId} sensors={sensors} collisionDetection={closestCenter} autoScroll={AUTO_SCROLL} onDragEnd={onDragEnd}>
        <SortableContext items={ids} strategy={verticalListSortingStrategy}>
          <ul className="border-b border-foreground/8">
            {list.map((c) => <ColumnRow key={c.id} id={c.id} on={c.on} onToggle={() => toggle(c.id)} />)}
          </ul>
        </SortableContext>
      </DndContext>
    </section>
  );
}

// One column: the handle (shown columns only: the order is theirs), the checkbox and its heading, the
// heading's hover as a second line. A hidden column is still a place a shown one can be dropped.
function ColumnRow({ id, on, onToggle }: { id: string; on: boolean; onToggle: () => void }) {
  const spec = columnSpec(id);
  const label = spec?.label ?? id;
  const { setNodeRef, setActivatorNodeRef, attributes, listeners, transform, transition, isDragging } =
    useSortable({ id, disabled: { draggable: !on, droppable: false } });
  const box = `col-${id}`;
  return (
    <li ref={setNodeRef} style={{ transform: CSS.Translate.toString(transform), transition }}
      className={`flex items-center gap-1 border-t border-foreground/8 bg-popover ${isDragging ? "relative z-10 rounded-md shadow-[0_8px_20px_-8px_rgb(0_0_0/0.35)]" : ""}`}>
      <button type="button" ref={setActivatorNodeRef} {...attributes} {...listeners} disabled={!on}
        aria-label={`reorder ${label}`} data-base-ui-swipe-ignore data-drag-handle
        className="flex size-11 shrink-0 cursor-grab touch-none items-center justify-center rounded-md text-foreground/45 transition-[color,opacity] hover:text-foreground focus-visible:outline-2 active:cursor-grabbing disabled:cursor-default disabled:opacity-0">
        <GripVertical className="size-4" />
      </button>
      <label htmlFor={box} className="flex min-h-11 min-w-0 flex-1 cursor-pointer items-center gap-3 py-1.5 pr-1">
        <Checkbox id={box} checked={on} onCheckedChange={onToggle} />
        <span className="min-w-0 flex-1">
          <span className={`block font-display text-[0.9375rem] leading-tight font-semibold ${on ? "text-foreground" : "text-foreground/55"}`}>{label}</span>
          {spec && <span className={`block truncate text-xs ${on ? "text-muted-foreground" : "text-foreground/45"}`} title={spec.title}>{spec.title}</span>}
        </span>
      </label>
    </li>
  );
}

// ---- Filters ---------------------------------------------------------------------------------------

function FiltersSection({ filters, setFilters }: { filters: ViewFilter[]; setFilters: (f: ViewFilter[]) => void }) {
  const used = new Set(filters.map((f) => f.field));
  // the row just added takes the focus (a number to type); the menu hands none back to its trigger
  const [added, setAdded] = useState<string | null>(null);
  const add = (field: string) => {
    const f: ViewFilter = field === "injury" ? { field, op: "in", value: ["healthy"] }
      : field === "avail" ? { field, op: "in", value: ["fa", "waivers"] }
      : field === "team" ? { field, op: "in", value: [] }
      : { field, op: "ge", value: NaN };
    setFilters(setFilter(filters, f));
    setAdded(field);
  };
  const numeric = PLAYER_COLUMNS.filter((c) => !used.has(c.id));
  const sets = Object.keys(SET_NAMES).filter((k) => !used.has(k));
  return (
    <section aria-labelledby="view-filters">
      <h3 id="view-filters" className={`mb-1.5 ${LABEL}`}>Filters</h3>
      {filters.length === 0 && <p className="border-t border-foreground/8 py-3 text-sm text-muted-foreground">No filters: every player at the position.</p>}
      {filters.length > 0 && (
        <ul className="border-b border-foreground/8">
          {filters.map((f) => (
            <FilterRow key={f.field} f={f} autoFocus={added === f.field}
              onChange={(next) => setFilters(setFilter(filters, next))} onRemove={() => setFilters(removeFilter(filters, f.field))} />
          ))}
        </ul>
      )}
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button variant="outline" className="mt-3 h-10 gap-1.5 border-foreground/15 sm:h-8" />} disabled={!numeric.length && !sets.length}>
          <Plus aria-hidden />
          Add filter
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" finalFocus={false} className="max-h-[min(24rem,var(--available-height))] w-auto min-w-48">
          {sets.map((k) => <DropdownMenuItem key={k} onClick={() => add(k)} className="min-h-10 sm:min-h-8">{SET_NAMES[k]}</DropdownMenuItem>)}
          {sets.length > 0 && numeric.length > 0 && <DropdownMenuSeparator />}
          {numeric.map((c) => (
            <DropdownMenuItem key={c.id} onClick={() => add(c.id)} className="min-h-10 sm:min-h-8">
              {c.label}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </section>
  );
}

function FilterRow({ f, autoFocus, onChange, onRemove }: { f: ViewFilter; autoFocus: boolean; onChange: (f: ViewFilter) => void; onRemove: () => void }) {
  const name = fieldName(f.field);
  const wide = f.op === "in";
  return (
    <li className={`flex flex-wrap items-center gap-x-2 gap-y-1.5 border-t border-foreground/8 py-2.5`}>
      <span className={`min-w-0 font-display text-[0.9375rem] font-semibold ${wide ? "flex-1" : "w-20 shrink-0 truncate"}`}>{name}</span>
      {!wide && <NumberControl f={f} autoFocus={autoFocus} onChange={onChange} />}
      <Button variant="ghost" size="icon" onClick={onRemove} aria-label={`remove the ${name} filter`} className={`${ICON_BTN} ${wide ? "" : "ml-auto"}`}>
        <X />
      </Button>
      {f.op === "in" && f.field !== "team" && (
        <SetControl field={f.field} value={f.value} onChange={(value) => onChange({ ...f, value })} />
      )}
      {f.op === "in" && f.field === "team" && (
        <TeamControl value={f.value} autoFocus={autoFocus} onChange={(value) => onChange({ ...f, value })} />
      )}
    </li>
  );
}

// at least / at most and the number; the input is uncontrolled so "5." or "-" mid-typing survives,
// and an empty one is a NaN the Apply leaves out
function NumberControl({ f, autoFocus, onChange }: { f: Extract<ViewFilter, { op: "ge" | "le" }>; autoFocus: boolean; onChange: (f: ViewFilter) => void }) {
  const name = fieldName(f.field);
  // the first value only: Base UI warns when an uncontrolled field's default moves after it mounts
  const [initial] = useState(Number.isFinite(f.value) ? String(f.value) : "");
  return (
    <div className="flex items-center gap-2">
      <ToggleGroup value={[f.op]} onValueChange={(v: string[]) => { if (v[0] === "ge" || v[0] === "le") onChange({ ...f, op: v[0] }); }}
        spacing={0} aria-label={`${name}: at least or at most`}>
        <ToggleGroupItem value="ge" variant="outline" aria-label="at least" className={`${PRESSABLE} text-base`}>≥</ToggleGroupItem>
        <ToggleGroupItem value="le" variant="outline" aria-label="at most" className={`${PRESSABLE} text-base`}>≤</ToggleGroupItem>
      </ToggleGroup>
      {/* no inputMode: the iOS decimal pad has no minus key, and beat, pace, Δ roster, Δ pool and air% are signed */}
      <Input type="number" step="any" autoFocus={autoFocus} aria-label={`${name} value`}
        defaultValue={initial}
        onChange={(e) => onChange({ ...f, value: e.currentTarget.value.trim() === "" ? NaN : e.currentTarget.valueAsNumber })}
        className="h-10 w-20 border-foreground/15 text-right tabular-nums sm:h-8" />
      <span className="w-3 text-sm text-muted-foreground">{PERCENT.has(f.field) ? "%" : ""}</span>
    </div>
  );
}

// injury / availability: any of the buckets, never none (the last one on stays on)
function SetControl({ field, value, onChange }: { field: "injury" | "avail"; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <ToggleGroup multiple value={value} onValueChange={(v: string[]) => { if (v.length) onChange(v); }}
      aria-label={fieldName(field)} className="w-full flex-wrap gap-1.5">
      {FILTER_SETS[field].map(([key, label]) => (
        <ToggleGroupItem key={key} value={key} variant="outline" className={`${PRESSABLE} text-[0.8125rem]`}>{label}</ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

function TeamControl({ value, autoFocus, onChange }: { value: string[]; autoFocus: boolean; onChange: (v: string[]) => void }) {
  const anchor = useComboboxAnchor();
  return (
    <Combobox multiple items={CLUBS} value={value} onValueChange={(v: string[]) => onChange(v)}>
      <ComboboxChips ref={anchor} className="min-h-10 w-full border-foreground/15 sm:min-h-8">
        <ComboboxValue>
          {(values: string[]) => (
            <>
              {values.map((v) => <ComboboxChip key={v} aria-label={v}>{v}</ComboboxChip>)}
              <ComboboxChipsInput autoFocus={autoFocus} aria-label="add a club" placeholder={values.length ? "" : "Pick clubs"} className="h-8 text-base sm:text-sm" />
            </>
          )}
        </ComboboxValue>
      </ComboboxChips>
      <ComboboxContent anchor={anchor}>
        <ComboboxEmpty>No club by that name.</ComboboxEmpty>
        <ComboboxList>
          {(club: string) => <ComboboxItem key={club} value={club}>{club} <span className="text-muted-foreground">{TEAMS[club].name}</span></ComboboxItem>}
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  );
}

// ---- Presets ---------------------------------------------------------------------------------------

function PresetsSection({ onDone }: { onDone: () => void }) {
  const { presets, defaultId, presetId, go } = usePlayerView();
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const run = async (id: string, fn: () => Promise<PresetStore>, after?: (s: PresetStore) => void) => {
    setBusy(id);
    setErrors((x) => Object.fromEntries(Object.entries(x).filter(([k]) => k !== id)));
    try {
      const store = await fn();
      if (after) after(store);
      else router.refresh();
    } catch (e) {
      setErrors((x) => ({ ...x, [id]: e instanceof ClientError ? e.message : String(e) }));
    } finally {
      setBusy(null);
    }
  };
  const rename = (p: Preset, name: string) => run(p.id, () => clientPost<PresetStore>(`/players/presets/${p.id}`, { name }));
  const makeDefault = (p: Preset) => run(p.id, () => clientPost<PresetStore>("/players/presets/default", { id: p.id }));
  const remove = (p: Preset) => run(p.id, () => clientPost<PresetStore>(`/players/presets/${p.id}/delete`), (store) => {
    if (p.id !== presetId) return router.refresh();
    // the one on screen is gone: land on the default the store now names
    const def = store.presets.find((x) => x.id === store.default) ?? store.presets[0];
    go(presetView(def), { presetId: def.id, base: presetView(def) });
    onDone();
  });
  return (
    <>
      <div className="mb-1.5 flex items-baseline justify-between gap-3">
        <h3 className={LABEL}>Presets</h3>
        <span className="text-xs text-muted-foreground">saved as you change them</span>
      </div>
      <ul className="border-b border-foreground/8">
        {presets.map((p) => (
          <PresetRow key={p.id} p={p} isDefault={p.id === defaultId} active={p.id === presetId} only={presets.length === 1}
            busy={busy !== null} error={errors[p.id] ?? null}
            onRename={(name) => rename(p, name)} onDefault={() => makeDefault(p)} onDelete={() => remove(p)} />
        ))}
      </ul>
    </>
  );
}

function PresetRow({ p, isDefault, active, only, busy, error, onRename, onDefault, onDelete }: {
  p: Preset; isDefault: boolean; active: boolean; only: boolean; busy: boolean; error: string | null;
  onRename: (name: string) => void; onDefault: () => void; onDelete: () => void;
}) {
  // Delete takes two taps: the first arms it for three seconds
  const [armed, setArmed] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  const del = () => {
    if (timer.current) clearTimeout(timer.current);
    if (armed) { setArmed(false); onDelete(); return; }
    setArmed(true);
    timer.current = setTimeout(() => setArmed(false), 3000);
  };
  const commit = (el: HTMLInputElement) => {
    const name = el.value.trim();
    if (!name) { el.value = p.name; return; }
    if (name !== p.name) onRename(name);
  };
  return (
    <li className="border-t border-foreground/8 py-2">
      <div className="flex items-center gap-2">
        {/* keyed on the saved name: a refresh after a rename puts the server's spelling back in */}
        <Input key={p.name} defaultValue={p.name} maxLength={40} aria-label={`rename ${p.name}`} disabled={busy}
          onBlur={(e) => commit(e.currentTarget)}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
            if (e.key === "Escape") { e.currentTarget.value = p.name; e.currentTarget.blur(); e.stopPropagation(); }
          }}
          className={`h-10 min-w-0 flex-1 border-foreground/10 bg-transparent px-2 font-display text-base font-semibold md:text-[0.9375rem] shadow-none hover:border-foreground/15 focus-visible:border-ring sm:h-8 dark:bg-transparent ${active ? "" : "text-foreground/80"}`} />
        <Toggle variant="outline" pressed={isDefault} disabled={busy || isDefault} onPressedChange={(on) => { if (on) onDefault(); }}
          aria-label={isDefault ? `${p.name} is the default` : `make ${p.name} the default`}
          className={`${PRESSABLE} font-display text-[0.75rem] font-semibold uppercase tracking-[0.12em] disabled:opacity-100`}>
          default
        </Toggle>
        <Button variant={armed ? "destructive" : "ghost"} size={armed ? "default" : "icon"} onClick={del} disabled={busy || only}
          aria-label={armed ? `confirm: delete ${p.name}` : `delete ${p.name}`}
          className={armed ? "h-10 px-3 sm:h-8" : ICON_BTN}>
          {armed ? "Delete?" : <Trash2 />}
        </Button>
      </div>
      {error && <p role="alert" className="mt-1 px-2 text-xs text-destructive">{error}</p>}
    </li>
  );
}
