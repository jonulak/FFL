"use client";

import {
  DndContext, DragOverlay, KeyboardSensor, MeasuringStrategy, MouseSensor, TouchSensor, closestCenter, pointerWithin, useDroppable, useSensor,
  useSensors, type CollisionDetection, type DragCancelEvent, type DragEndEvent, type DragOverEvent, type DragStartEvent, type UniqueIdentifier,
} from "@dnd-kit/core";
import { SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Check, ChevronUp, GripVertical, LoaderCircle, Plus, Trash2, TriangleAlert, X } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Combobox, ComboboxContent, ComboboxEmpty, ComboboxInput, ComboboxItem, ComboboxList } from "@/components/ui/combobox";
import { Label } from "@/components/ui/label";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import type { ClaimErr, ClaimGroup, ClaimPlayer, PoolStatus } from "@/lib/api";
import {
  addCut, capacity, cutBlock, displayTargets, groupHasFA, groupPos, holder, moveGroup, newGroup, place, processDay, prune,
  regroupKeys, removeFrom, removeGroup, setOpen, statusTag,
} from "@/lib/claims";
import { pts, signed } from "@/lib/format";
import { useClaims } from "./claims-context";
import { ClaimName } from "./claim-name";
import { useClaimsFlip } from "./claims-flip";
import { ClaimsSubmit, PHONE_ACTION, PHONE_INSET, ReviewBody, TestModeAlert } from "./claims-submit";
import { Clock } from "./local-time";
import { Presence, Swap } from "./motion";

const LABEL = "font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground";
const SUB = "font-display text-[0.6875rem] font-semibold uppercase tracking-[0.14em] text-foreground/55";
// a piece outside the groups that comes or changes fades in (tw-animate)
const FADE = "animate-in fade-in duration-200 motion-reduce:animate-none";
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
    <button type="button" ref={handleRef} {...attributes} {...listeners} disabled={disabled} aria-label={label} data-base-ui-swipe-ignore
      className={`${HIT} flex size-6 cursor-grab touch-none items-center justify-center rounded-sm text-foreground/40 transition-[color,opacity] hover:text-foreground focus-visible:outline-2 active:cursor-grabbing disabled:cursor-default disabled:opacity-40`}>
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

// A line's face: rank, name with its tags, the number. The sortable row and the copy that rides
// under the pointer while it is dragged both draw it.
type Face = { pid: number; rank: ReactNode; tags?: ReactNode; value: ReactNode; tone?: string };
function FaceCells({ pid, rank, tags, value, tone = "" }: Face) {
  return (
    <>
      <span className="text-right font-display text-sm font-semibold tabular-nums text-muted-foreground">{rank}</span>
      <span className="flex min-w-0 flex-wrap items-center gap-x-1.5">
        <ClaimName id={pid} className={`font-medium ${tone}`} />
        {tags}
      </span>
      <span className="text-right text-xs tabular-nums text-foreground/70">{value}</span>
    </>
  );
}

// One sortable line: grip, the face, remove. While it is dragged it stays behind as a dashed
// slot where it will land; the row itself rides in the overlay.
function Line({ id, name, onRemove, removeLabel, disabled, ...face }: Face & {
  id: string; name: string; onRemove: () => void; removeLabel: string; disabled: boolean;
}) {
  const { setNodeRef, setActivatorNodeRef, attributes, listeners, transform, transition, isDragging } = useSortable({ id, disabled });
  return (
    <li ref={setNodeRef} data-row={id} data-lifted={isDragging || undefined} style={{ transform: CSS.Translate.toString(transform), transition }}
      className={`${ROW} ${isDragging ? "rounded-md border-transparent bg-foreground/[0.04] outline-1 -outline-offset-1 outline-foreground/25 outline-dashed *:invisible" : ""}`}>
      <Grip label={`Move ${name}`} handleRef={setActivatorNodeRef} attributes={attributes} listeners={listeners} disabled={disabled} />
      <FaceCells {...face} />
      <Remove label={removeLabel} onClick={onRemove} disabled={disabled} />
    </li>
  );
}

// The dragged line, lifted off the card: the same grid on the card's surface, a deeper shadow.
function Lifted(face: Face) {
  return (
    <div className={`${ROW} cursor-grabbing rounded-md border-t-0 bg-card ring-1 ring-foreground/10 shadow-[0_14px_28px_-12px_rgb(0_0_0_/_0.55)]`}>
      <span className="flex size-6 items-center justify-center text-foreground"><GripVertical aria-hidden className="size-4" /></span>
      <FaceCells {...face} />
      <span />
    </div>
  );
}

type Lookup = (id: number) => ClaimPlayer | undefined;
function targetFace(player: Lookup, id: number, now: boolean, rank: number): Face {
  const pl = player(id);
  const tag = statusTag(pl);
  return {
    pid: id, rank,
    tags: now
      ? <Badge variant="default" shape="chip" className="h-4 px-1 text-[0.6875rem]" title="a free agent: added the moment you submit, ahead of the claims">now</Badge>
      : tag && <span className="font-display text-[0.6875rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{tag}</span>,
    value: pl?.add != null ? <>add <span className="font-display text-sm font-semibold text-foreground">{signed(pl.add)}</span></> : null,
  };
}
function cutFace(player: Lookup, id: number, hasFA: boolean, rank: number): Face {
  const pl = player(id);
  const block = pl ? cutBlock(pl, hasFA) : null;
  return {
    pid: id, rank, tone: "text-out",
    tags: block && <span className="text-xs text-destructive">{block}</span>,
    value: pl?.ros_adj != null ? `ROS ${pts(pl.ros_adj)}` : null,
  };
}

// Drag ids: `g<key>` a group (its card key, so the id follows the group when the groups reorder),
// `t<player>` a target, `c<key>_<player>` a cut (one player can be a cut in several groups), and
// `zt<i>` / `zc<i>` group i's targets / make-room area, so a group with an empty list still takes a drop.
type Kind = "g" | "t" | "c";
const kindOf = (id: UniqueIdentifier) => String(id)[0] as Kind | "z";
const numOf = (id: UniqueIdentifier) => Number(String(id).replace(/^z?[gtc]/, "").replace(/^\d+_/, ""));
const cutKey = (id: UniqueIdentifier) => Number(String(id).slice(1).split("_")[0]);
const listOf = (k: Kind) => (k === "t" ? "targets" : "cuts");
// what a droppable takes: a group takes groups, a row or an area its own list
const takes = (id: UniqueIdentifier): Kind => (kindOf(id) === "z" ? (String(id)[1] as Kind) : (kindOf(id) as Kind));

// A group drags among the groups, a target among targets, a cut among cuts. A row under the
// pointer wins over its group's area; the keyboard (no pointer) goes to the nearest row.
const collide: CollisionDetection = (args) => {
  const k = kindOf(args.active.id) as Kind;
  const pool = args.droppableContainers.filter((c) => takes(c.id) === k);
  if (k === "g") return closestCenter({ ...args, droppableContainers: pool });
  const hits = pointerWithin({ ...args, droppableContainers: pool });
  const row = hits.find((h) => kindOf(h.id) !== "z");
  if (row) return [row];
  if (hits.length) return hits;
  return closestCenter({ ...args, droppableContainers: pool.filter((c) => kindOf(c.id) !== "z") });
};

function Zone({ id, children }: { id: string; children: ReactNode }) {
  const { setNodeRef } = useDroppable({ id });
  return <div ref={setNodeRef}>{children}</div>;
}

// An empty list's place: at rest a faint dashed box saying what goes there; while a row of its
// kind is dragged, the slot he would land in, firmer, its words crossfading. One size either way,
// so lifting a row moves nothing in the list (a slot opening above him would shift his row from
// under the finger, and dnd-kit takes its one measure of him after that).
function EmptySlot({ fx, open, rest, drop }: { fx: string; open: boolean; rest: string; drop: string }) {
  const fade = "col-start-1 row-start-1 transition-opacity duration-200";
  return (
    <p data-fx={fx} className={`my-1 grid h-8 place-items-center rounded-md px-2 text-center text-xs outline-1 -outline-offset-1 outline-dashed transition-[outline-color,background-color,color] duration-200 pointer-coarse:h-11 ${open ? "bg-foreground/[0.03] text-foreground/75 outline-foreground/35" : "text-muted-foreground outline-foreground/12"}`}>
      <span className={`${fade} ${open ? "opacity-0" : ""}`} aria-hidden={open}>{rest}</span>
      <span className={`${fade} ${open ? "" : "opacity-0"}`} aria-hidden={!open}>{drop}</span>
    </p>
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

// A group's title and its "get K of N".
function GroupTitle({ g, gi, fx }: { g: ClaimGroup; gi: number; fx?: string }) {
  const { pos } = useClaims();
  const p = groupPos(g, pos);
  return (
    <>
      <h3 className="font-display text-xl font-bold leading-none tracking-tight">
        {gi + 1}{p && <span className="text-foreground/80"> · {p}</span>}
      </h3>
      {g.targets.length > 0 && (
        <Badge variant="close" shape="chip" className="ml-auto" data-fx={fx && `${fx}|${capacity(g)}:${g.targets.length}`}>
          get {Math.min(capacity(g), g.targets.length)} of {g.targets.length}
        </Badge>
      )}
    </>
  );
}

// Who the group is after, by name: the line a folded card keeps so the groups can be told apart.
function GroupNames({ g, className = "" }: { g: ClaimGroup; className?: string }) {
  const { status, player } = useClaims();
  const names = displayTargets(g, status).map((t) => player(t.id)?.name ?? `player ${t.id}`);
  return (
    <p className={`mt-1.5 pl-[1.875rem] text-xs leading-snug text-muted-foreground ${className}`}>
      {names.length ? names.join(" · ") : "no targets yet"}
    </p>
  );
}

// The dragged group, lifted off the list: the folded card on the panel's one deep drop.
function LiftedGroup({ g, gi }: { g: ClaimGroup; gi: number }) {
  return (
    <Card size="sm" className="cursor-grabbing gap-0 rounded-lg px-3 py-2.5 ring-1 ring-foreground/10 shadow-[0_14px_28px_-12px_rgb(0_0_0_/_0.55)]">
      <div className="flex items-center gap-1.5">
        <span className="flex size-6 items-center justify-center text-foreground"><GripVertical aria-hidden className="size-4" /></span>
        <GroupTitle g={g} gi={gi} />
        <span className={`size-6 ${g.targets.length ? "" : "ml-auto"}`} />
      </div>
      <GroupNames g={g} />
    </Card>
  );
}

// One group: "get K of N", the targets best first (free agents on top: they are added the moment
// you submit), then the rows that make room, first to go first. Its grip reorders the groups; a
// target's or a cut's grip moves him within the group or into another one. `g` is the group as
// the drag shows it, `dragging` the list a row is being dragged in, `landing` the group a dragged
// row has moved into, `cutId` the drag id of each of its cuts. While a group is dragged the list
// folds every card to its title and its targets' names (`[data-folded]` in globals.css), so all of
// them are in view and alike in height; the body stays mounted, and `reveal` fades it back in once
// the dragged one has landed (its grip waits for that: a pickup measures the fold from open cards).
function GroupCard({ g, gi, gkey, roster, dragging, landing, reveal, settling, cutId, errors }: {
  g: ClaimGroup; gi: number; gkey: number; roster: [number, ClaimPlayer][]; dragging: Kind | null; landing: boolean;
  reveal: boolean; settling: boolean; cutId: (c: number) => string; errors: ClaimErr[];
}) {
  const { groups, setGroups, status, player, data, submitting } = useClaims();
  const { setNodeRef, setActivatorNodeRef, attributes, listeners, transform, transition, isDragging } = useSortable({ id: `g${gkey}`, disabled: submitting || settling });
  const shown = displayTargets(g, status);
  const fa = shown.filter((t) => t.now);
  const waiting = shown.filter((t) => !t.now);
  const hasFA = groupHasFA(g, status);
  const name = (id: number) => player(id)?.name ?? `player ${id}`;

  const targetLine = (t: { id: number; now: boolean }, rank: number) => (
    <Line key={t.id} id={`t${t.id}`} name={name(t.id)} disabled={submitting} {...targetFace(player, t.id, t.now, rank)}
      onRemove={() => setGroups(removeFrom(groups, gi, "targets", t.id))} removeLabel={`Remove ${name(t.id)} from group ${gi + 1}`} />
  );

  return (
    <li ref={setNodeRef} data-group={gkey} style={{ transform: CSS.Translate.toString(transform), transition }}>
      <Card size="sm" className={`gap-0 rounded-lg px-3 py-2.5 transition-[box-shadow,background-color] duration-200 shadow-[0_6px_18px_-14px_rgb(0_0_0_/_0.45)] ${isDragging ? "bg-foreground/[0.04] shadow-none ring-0 outline-1 -outline-offset-1 outline-foreground/25 outline-dashed *:invisible" : ""} ${landing ? "ring-2 ring-foreground/35" : ""}`}>
        <div className="flex items-center gap-1.5">
          <Grip label={`Move group ${gi + 1}`} handleRef={setActivatorNodeRef} attributes={attributes} listeners={listeners} disabled={submitting} />
          <GroupTitle g={g} gi={gi} fx={`k${gkey}`} />
          <Button variant="ghost" size="icon-xs" aria-label={`Delete group ${gi + 1}`} disabled={submitting}
            onClick={() => setGroups(removeGroup(groups, gi))} className={`${HIT} text-foreground/45 hover:text-destructive ${g.targets.length ? "" : "ml-auto"}`}>
            <Trash2 aria-hidden />
          </Button>
        </div>
        <GroupNames g={g} className="group-names" />

        <div className={`group-body ${reveal ? "group-reveal" : ""}`}>
        <Zone id={`zt${gi}`}>
          <p className={`${SUB} mt-3 mb-0.5`}>targets · best first</p>
          {g.targets.length === 0 && (
            <EmptySlot fx={`t${gkey}`} open={dragging === "t"} rest="Pick targets with the claim button on any row" drop="Drop to move him into this group" />
          )}
          <SortableContext id={`fa${gi}`} items={fa.map((t) => `t${t.id}`)} strategy={verticalListSortingStrategy}>
            <ol>{fa.map((t, i) => targetLine(t, i + 1))}</ol>
          </SortableContext>
          <SortableContext id={`w${gi}`} items={waiting.map((t) => `t${t.id}`)} strategy={verticalListSortingStrategy}>
            <ol>{waiting.map((t, i) => targetLine(t, fa.length + i + 1))}</ol>
          </SortableContext>
        </Zone>

        <Zone id={`zc${gi}`}>
          <p className={`${SUB} mt-3 mb-0.5`}>make room · first to go first</p>
          <ol>
            {g.open > 0 && (
              <li data-fx={`o${gkey}`} className={ROW}>
                <span />
                <span className="text-right font-display text-sm font-semibold tabular-nums text-muted-foreground">{g.open > 1 ? `1–${g.open}` : 1}</span>
                <span className="text-foreground/70 italic">open roster spot{g.open > 1 && ` ×${g.open}`}</span>
                <span />
                <span />
              </li>
            )}
          </ol>
          {/* with no open spot in use the box stands at rest too, saying the group needs a cut */}
          {g.cuts.length === 0 && (g.open === 0 || dragging === "c") && (
            <EmptySlot fx={`c${gkey}`} open={dragging === "c"} rest={g.targets.length ? "Add a cut: the group gets one target per cut" : "No cuts yet"}
              drop="Drop to cut him for this group" />
          )}
          <SortableContext id={`cuts${gi}`} items={g.cuts.map(cutId)} strategy={verticalListSortingStrategy}>
            <ol>
              {g.cuts.map((c, i) => (
                <Line key={c} id={cutId(c)} name={name(c)} disabled={submitting} {...cutFace(player, c, hasFA, g.open + i + 1)}
                  onRemove={() => setGroups(removeFrom(groups, gi, "cuts", c))} removeLabel={`Remove ${name(c)} from the cuts`} />
              ))}
            </ol>
          </SortableContext>
        </Zone>
        <CutPicker gi={gi} roster={roster} used={new Set(g.cuts)} hasFA={hasFA} disabled={submitting} />
        {data.open_spots > 0 && (
          <Label className="mt-2.5 text-xs font-normal text-foreground/80">
            <Checkbox checked={g.open > 0} disabled={submitting}
              onCheckedChange={(on) => setGroups(setOpen(groups, gi, on, data.open_spots))} />
            use the {data.open_spots} open roster spot{data.open_spots > 1 && "s"}
          </Label>
        )}
        {errors.length > 0 && (
          <ul data-fx={`e${gkey}|${errors.map((e) => `${e.player_id}:${e.reason}`).join(";")}`} className="mt-2 space-y-0.5 text-xs text-destructive" aria-live="polite">
            {errors.map((e, i) => <li key={i}>{e.player_id != null && <span className="font-medium">{name(e.player_id)}: </span>}{e.reason}</li>)}
          </ul>
        )}
        </div>
      </Card>
    </li>
  );
}

// Where the plan stands against ESPN: its claim list unreadable, never sent, changed since the
// last send, ESPN's list no longer the plan's claims (on_espn: checked against ESPN, not the record),
// withdrawn (an empty plan sent), or on ESPN. In test mode (writes false) the holder is the fake, never ESPN.
function SubmitState() {
  const { data } = useClaims();
  const who = data.writes ? "ESPN" : "the fake";
  // a new state fades in where the old one stood
  const fade = (state: string, badge: ReactNode) => <span key={state} className={`flex min-w-0 ${FADE}`}>{badge}</span>;
  if (data.pending_error) return fade("error", <Badge variant="destructive" className="max-w-full truncate" title={data.pending_error}>ESPN: {data.pending_error}</Badge>);
  if (!data.last_submit) return fade("unsent", <Badge variant="muted">not sent</Badge>);
  if (data.edited_since_submit) return fade("edited", <Badge variant="warn">edited since submit</Badge>);
  if (!data.on_espn) return fade("differs", <Badge variant="warn">differs from {who}</Badge>);
  if (data.last_submit.claims.length === 0 && data.last_submit.now.length === 0)
    return fade("withdrawn", <Badge variant="muted"><Check aria-hidden className="stroke-[2.5]" />withdrawn <Clock iso={data.last_submit.at} /></Badge>);
  return fade("sent", <Badge variant="muted"><Check aria-hidden className="stroke-[2.5]" />{data.writes ? "on ESPN" : "sent to the fake"} · <Clock iso={data.last_submit.at} /></Badge>);
}

// The roster errors (group null): an IR player with no injury designation, whom ESPN refuses every add
// and claim for. Shown above the groups, since no card is his.
function RosterBlock() {
  const { data, player } = useClaims();
  const errors = data.errors.filter((e) => e.group == null);
  if (!errors.length) return null;
  return (
    <Alert variant="destructive" className="border-destructive/40 bg-destructive/10">
      <TriangleAlert aria-hidden />
      <AlertTitle>Can&apos;t send until the roster is legal</AlertTitle>
      <AlertDescription>
        <ul className="space-y-0.5">
          {errors.map((e, i) => (
            <li key={i}>{e.player_id != null && <span className="font-medium">{player(e.player_id)?.name ?? `player ${e.player_id}`} </span>}{e.reason}. Move him off IR on the Roster page first.</li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}

// A group's list in the order the panel shows it: targets FA block first, cuts as stored.
const order = (g: ClaimGroup, list: "targets" | "cuts", status: Record<string, PoolStatus>) =>
  list === "targets" ? displayTargets(g, status).map((t) => t.id) : g.cuts;

// The panel's body, shared by the docked column and the phone's sheet. `heading` is the title
// element each host needs (an h2, or the sheet's labelled title).
//
// The card keys for the groups (regroupKeys), held across renders.
function useGroupKeys(groups: ClaimGroup[]): number[] {
  const [k, setK] = useState(() => ({ groups, ...regroupKeys([], [], groups, 0) }));
  if (k.groups === groups) return k.keys;
  const next = { groups, ...regroupKeys(k.groups, k.keys, groups, k.next) };
  setK(next);
  return next.keys;
}

// The nearest ancestor that scrolls: the sheet's pane, the docked column, else the page.
function scroller(el: HTMLElement): HTMLElement {
  for (let p = el.parentElement; p; p = p.parentElement) {
    const o = getComputedStyle(p).overflowY;
    if ((o === "auto" || o === "scroll") && p.scrollHeight > p.clientHeight) return p;
  }
  return (document.scrollingElement ?? document.documentElement) as HTMLElement;
}

const DROP_MS = 240;
const DROP = { duration: DROP_MS, easing: "cubic-bezier(0.16, 1, 0.3, 1)" };
const REVEAL_MS = 300;
const REDUCE = "(prefers-reduced-motion: reduce)";
const stillMotion = () => matchMedia(REDUCE).matches;
const onMotionChange = (fn: () => void) => {
  const m = matchMedia(REDUCE);
  m.addEventListener("change", fn);
  return () => m.removeEventListener("change", fn);
};
// the cards fold and open mid-drag: measure the drop targets again on every change, not once at the start
const MEASURE = { droppable: { strategy: MeasuringStrategy.Always } };
const noop = () => () => {};

// A drag: what is moving, the plan when it began, and for a row the group he left (`home`), the
// group he is in now (`at`) and the plan as the drag shows it (`preview`).
type Drag = { id: UniqueIdentifier; before: ClaimGroup[]; home: number | null; at: number | null; preview: ClaimGroup[] | null };

// One drag context spans every group, so a row can leave its group. While a row is dragged the
// plan shown is a preview: crossing into another group moves him there at once (its list opens a
// slot under the pointer, both "get K of N" counts follow), and only the drop writes the plan,
// pruning a group the move emptied. Escape puts everything back.
//
// A group is dragged among folded cards. Folding moves the dragged card's slot up by everything
// that folded above it, which would leave the pointer over another card, and shrinks the list,
// which shrinks the sheet under it. So the pickup folds the list at once, by hand, to measure
// that, and the first render of the drag pads the list by it and holds its height: dnd-kit's
// first look at the card (which it keeps) finds it folded and where it was lifted. The cards stay
// folded while the lifted one flies to its slot, then open, scrolled so it stays where it landed.
//
// The lifted copy is portalled to the body: the phone's sheet is transformed, which would make it
// the frame a fixed overlay is placed in, and draw the copy off the pointer by the sheet's top.
function PanelBody({ heading, onReview, sheet = false }: { heading: (text: string) => ReactNode; onReview: () => void; sheet?: boolean }) {
  const { data, groups, setGroups, player, status, dirty, saving, error, submitting } = useClaims();
  // the error keeps its words while it folds away
  const [lastError, setLastError] = useState(error);
  if (error && error !== lastError) setLastError(error);
  const sensors = useDragSensors();
  const dndId = useId();
  const keys = useGroupKeys(groups);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [settling, setSettling] = useState(false);
  const [reveal, setReveal] = useState(false);
  // while groups fold: the list's height when the drag began, held so the sheet does not shrink
  // under the pointer, and the room above the cards that keeps the dragged slot where it was
  const [hold, setHold] = useState<{ h: number; pad: number } | null>(null);
  const listRef = useRef<HTMLOListElement>(null);
  const anchor = useRef<{ key: number; top: number; s: HTMLElement } | null>(null);
  const flipShift = useRef(0); // the scroll the open below made, for the FLIP that follows it
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  useEffect(() => () => timers.current.forEach(clearTimeout), []);
  const still = useSyncExternalStore(onMotionChange, stillMotion, () => false);
  const client = useSyncExternalStore(noop, () => true, () => false);

  const shown = drag?.preview ?? groups;
  const roster = Object.entries(data.players)
    .filter(([, p]) => p.droppable != null) // roster players only: a free agent's slot reads "FA", his droppable null
    .map(([k, p]) => [Number(k), p] as [number, ClaimPlayer])
    .sort((a, b) => (a[1].ros_adj ?? 0) - (b[1].ros_adj ?? 0));
  const day = processDay(groups.flatMap((g) => g.targets).filter((t) => status[String(t)] !== "FREEAGENT").map((t) => player(t)?.waiver_date ?? null));
  const moves = data.now.length + data.claims.length;
  // An empty plan withdraws: a Send that cancels the claims sent from here (and, in the review, the
  // outside ones with the box ticked), so a pending claim can be taken back without ESPN's site.
  const fromApp = data.pending.filter((c) => c.from_app).length;
  const withdraw = moves === 0 && (fromApp > 0 || data.pending.length > 0);
  const blocked = dirty || saving || submitting || data.errors.length > 0 || (moves === 0 && !withdraw);

  const rowKind = drag && kindOf(drag.id) !== "g" ? (kindOf(drag.id) as Kind) : null;
  const dragged = drag && rowKind ? numOf(drag.id) : null;
  const folded = (drag != null && rowKind == null) || settling;
  // the dragged cut keeps his drag id in whichever group the preview has him
  const cutId = (gi: number) => (c: number) => (rowKind === "c" && dragged === c && drag?.at === gi ? String(drag.id) : `c${keys[gi]}_${c}`);

  // The saved plan's errors for each card, found by its content (its own index first): a card an
  // edit left alone keeps them while the edit saves, so no line drops out and back in; an edited
  // one waits for the save.
  const used = new Set<number>();
  const errorsOf = shown.map((g, i) => {
    const same = (j: number) => !used.has(j) && JSON.stringify(data.groups[j]) === JSON.stringify(g);
    const j = same(i) ? i : data.groups.findIndex((_, k) => same(k));
    if (j < 0) return [];
    used.add(j);
    return data.errors.filter((e) => e.group === j);
  });

  const cardTop = (key: number) => listRef.current?.querySelector(`[data-group="${key}"]`)?.getBoundingClientRect().top ?? null;
  // remember where a card stands before the cards fold or open, to put it back there after
  const pin = (key: number) => {
    const top = cardTop(key);
    anchor.current = top != null && listRef.current ? { key, top, s: scroller(listRef.current) } : null;
  };

  // the cards have opened after a group's drop: scroll so the dropped one stays where it landed
  useLayoutEffect(() => {
    const a = anchor.current;
    anchor.current = null;
    const top = a && cardTop(a.key);
    if (a && top != null && !folded) {
      const before = a.s.scrollTop;
      a.s.scrollTop += top - a.top;
      flipShift.current += a.s.scrollTop - before;
    }
  }, [folded]);
  // after the scroll above (layout effects run in order): every change of size or place plays out
  const capture = useClaimsFlip(listRef, scroller, flipShift, still);

  const onDragStart = ({ active }: DragStartEvent) => {
    capture();
    if (kindOf(active.id) === "g") {
      const ol = listRef.current;
      const key = numOf(active.id);
      const top = cardTop(key);
      if (ol && top != null) {
        // measured from the list's own top, which the held height keeps where it is, then put back:
        // the next render folds, holds and pads in one go
        const h = ol.offsetHeight;
        const from = top - ol.getBoundingClientRect().top;
        ol.style.minHeight = `${h}px`;
        ol.setAttribute("data-folded", "");
        const to = (cardTop(key) ?? top) - ol.getBoundingClientRect().top;
        ol.removeAttribute("data-folded");
        ol.style.minHeight = "";
        setHold({ h, pad: Math.max(0, from - to) });
      }
      setDrag({ id: active.id, before: groups, home: null, at: null, preview: null });
      return;
    }
    const k = kindOf(active.id) as Kind;
    const home = k === "c" ? keys.indexOf(cutKey(active.id)) : holder(groups, "targets", numOf(active.id));
    setDrag({ id: active.id, before: groups, home, at: home, preview: groups });
  };

  // Crossing into another group's list moves him there now, above or below the row he is over
  // (to the end of a list he is over but not on a row of); a move inside one group waits for the drop.
  const onDragOver = ({ active, over }: DragOverEvent) => {
    const k = kindOf(active.id) as Kind;
    if (k === "g" || !over || over.id === active.id || !drag?.preview || drag.at == null) return;
    const list = listOf(k);
    const id = numOf(active.id);
    const onRow = kindOf(over.id) !== "z";
    const to = !onRow ? numOf(over.id) : k === "c" ? keys.indexOf(cutKey(over.id)) : holder(drag.preview, list, numOf(over.id));
    if (to == null || to < 0 || to === drag.at) return;
    const rows = order(drag.preview[to], list, status);
    let i = rows.length;
    if (onRow) {
      const r = active.rect.current.translated;
      i = rows.indexOf(numOf(over.id)) + (r && r.top + r.height / 2 > over.rect.top + over.rect.height / 2 ? 1 : 0);
    }
    capture();
    setDrag({ ...drag, at: to, preview: place(drag.preview, status, list, id, to, i, drag.at) });
  };

  // A group's drop: the cards hold folded while the lifted one flies home, then open around it.
  const settle = (key: number) => {
    setSettling(true);
    timers.current.push(setTimeout(() => {
      pin(key);
      capture();
      setHold(null);
      setSettling(false);
      setReveal(true);
      timers.current.push(setTimeout(() => setReveal(false), REVEAL_MS));
    }, still ? 0 : DROP_MS));
  };

  const onDragEnd = ({ active, over }: DragEndEvent) => {
    capture();
    const k = kindOf(active.id) as Kind;
    const d = drag;
    setDrag(null);
    if (k === "g") {
      const from = keys.indexOf(numOf(active.id));
      const to = over ? keys.indexOf(numOf(over.id)) : -1;
      if (from >= 0 && to >= 0 && from !== to) setGroups(moveGroup(groups, from, to));
      settle(numOf(active.id));
      return;
    }
    if (!d?.preview || d.at == null) return;
    const list = listOf(k);
    const id = numOf(active.id);
    let next = d.preview;
    if (over && kindOf(over.id) === k && over.id !== active.id) {
      const gi = d.at;
      const og = k === "c" ? keys.indexOf(cutKey(over.id)) : holder(next, list, numOf(over.id));
      if (gi === og) next = place(next, status, list, id, gi, order(next[gi], list, status).indexOf(numOf(over.id)), gi);
    }
    if (JSON.stringify(next) !== JSON.stringify(d.before)) setGroups(prune(d.before, next));
  };

  const onDragCancel = ({ active }: DragCancelEvent) => {
    capture();
    setDrag(null);
    if (kindOf(active.id) === "g") settle(numOf(active.id));
  };

  const lifted = () => {
    if (drag && !rowKind) {
      const gi = keys.indexOf(numOf(drag.id));
      return gi >= 0 ? <LiftedGroup g={groups[gi]} gi={gi} /> : null;
    }
    if (!rowKind || dragged == null || drag?.at == null) return null;
    const g = shown[drag.at];
    const i = order(g, listOf(rowKind), status).indexOf(dragged);
    return rowKind === "t"
      ? <Lifted {...targetFace(player, dragged, status[String(dragged)] === "FREEAGENT", i + 1)} />
      : <Lifted {...cutFace(player, dragged, groupHasFA(g, status), g.open + i + 1)} />;
  };

  return (
    // no scroll anchoring (Chrome only): the fold and the drop place the cards themselves
    <div className="space-y-3 [overflow-anchor:none]">
      <div className="flex flex-wrap items-center justify-between gap-2 max-lg:pr-9">
        {heading(`Claims · process ${day}`)}
        {/* a save in flight turns beside the state, so no line opens over the groups and shuts again */}
        <span className="ml-auto flex items-center gap-1.5">
          <Presence show={saving}><span className="flex"><LoaderCircle role="status" aria-label="saving" className="size-3.5 animate-spin text-muted-foreground" /></span></Presence>
          <SubmitState />
        </span>
      </div>
      {!data.writes && <TestModeAlert />}
      <RosterBlock />
      {/* both fold out of the way when they go, so the groups below rise rather than jump */}
      <Presence show={error != null} fold>
        <p className={`text-xs ${(error ?? lastError ?? "").startsWith("saving") ? "text-muted-foreground" : "text-destructive"}`} aria-live="polite">{error ?? lastError}</p>
      </Presence>
      <Presence show={groups.length === 0} fold>
        <p className="max-w-[40ch] text-sm leading-relaxed text-muted-foreground">
          No claims this week. Press <span className="font-medium text-foreground">claim</span> on a player&apos;s row to start a group:
          list the players you&apos;d take, best first, then the roster spots they can fill.
        </p>
      </Presence>
      <DndContext id={dndId} sensors={sensors} collisionDetection={collide} measuring={MEASURE}
        onDragStart={onDragStart} onDragOver={onDragOver} onDragEnd={onDragEnd} onDragCancel={onDragCancel}>
        <SortableContext items={keys.map((k) => `g${k}`)} strategy={verticalListSortingStrategy}>
          <ol ref={listRef} data-folded={folded || undefined} className="space-y-2.5" style={hold ? { minHeight: hold.h, paddingTop: hold.pad } : undefined}>
            {shown.map((g, i) => (
              <GroupCard key={keys[i]} g={g} gi={i} gkey={keys[i]} roster={roster} dragging={rowKind} reveal={reveal} settling={settling} errors={errorsOf[i]}
                landing={drag?.at === i && drag.home !== i} cutId={cutId(i)} />
            ))}
          </ol>
        </SortableContext>
        {/* on the body, above the sheet (z-50): see the note above */}
        {client && createPortal(<DragOverlay className="z-[60]" dropAnimation={still ? null : DROP}>{lifted()}</DragOverlay>, document.body)}
      </DndContext>
      {/* In the sheet the actions stay pinned to its foot, thumb-height, above the home indicator;
          docked beside the table they follow the last group at desk size. */}
      <div className={sheet
        ? `sticky bottom-0 -mx-4 mt-4 flex items-center gap-3 border-t border-foreground/10 bg-popover px-4 pt-3 ${PHONE_INSET}`
        : "flex flex-wrap items-center gap-2 pt-1"}>
        <Button variant={sheet ? "outline" : "ghost"} size={sheet ? "default" : "sm"} disabled={submitting}
          className={sheet ? PHONE_ACTION : undefined} onClick={() => setGroups(newGroup(groups))}>
          <Plus aria-hidden />New group
        </Button>
        {(groups.length > 0 || withdraw) && (
          <Button variant={withdraw ? "outline" : "default"} className={sheet ? `flex-1 ${PHONE_ACTION}` : "ml-auto"} disabled={blocked} onClick={onReview}>
            {withdraw
              ? fromApp ? `Withdraw ${fromApp} claim${fromApp === 1 ? "" : "s"}` : "Withdraw claims"
              : `Review ${moves} move${moves === 1 ? "" : "s"}`}
          </Button>
        )}
      </div>
    </div>
  );
}

type Step = "claims" | "review";
const STAGE_MS = 420;

// The phone's claims: one bottom sheet with two steps. Review does not swap the sheet for a
// dialog: the sheet itself rises to full height and squares its corners (its height comes from
// its content, so it is measured either side of the change and run with WAAPI, a FLIP of the
// box) while the claims sink back and the review rises into the room (`.stage-in`/`.stage-out`
// in globals.css). Back runs it the other way. The step being left stays mounted, out of flow
// and inert, until it has faded, so the sheet's new height is measured off the step it shows.
function ClaimsSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { submitting } = useClaims();
  const [step, setStep] = useState<Step>("claims");
  const [leaving, setLeaving] = useState<Step | null>(null);
  const popup = useRef<HTMLDivElement>(null);
  const from = useRef<{ h: number; r: string } | null>(null);

  const go = (next: Step) => {
    const el = popup.current;
    // read mid-run too: Back during the rise starts from where the sheet is
    if (el) from.current = { h: el.getBoundingClientRect().height, r: getComputedStyle(el).borderTopLeftRadius };
    setLeaving(step);
    setStep(next);
  };

  useLayoutEffect(() => {
    const el = popup.current;
    const b = from.current;
    from.current = null;
    if (!el || !b || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const h = el.getBoundingClientRect().height;
    const r = getComputedStyle(el).borderTopLeftRadius;
    const run = el.animate(
      [{ height: `${b.h}px`, borderTopLeftRadius: b.r, borderTopRightRadius: b.r }, { height: `${h}px`, borderTopLeftRadius: r, borderTopRightRadius: r }],
      { duration: STAGE_MS, easing: "cubic-bezier(0.16, 1, 0.3, 1)" },
    );
    return () => run.cancel();
  }, [step]);

  useEffect(() => {
    if (!leaving) return;
    const t = setTimeout(() => setLeaving(null), STAGE_MS);
    return () => clearTimeout(t);
  }, [leaving, step]);

  const forward = step === "review";
  const paneClass = (s: Step) =>
    s === leaving ? "pointer-events-none absolute inset-0 stage-out" : leaving ? "col-start-1 row-start-1 stage-in" : "col-start-1 row-start-1";

  return (
    <Sheet open={open} onOpenChange={(o) => { if (!submitting) onOpenChange(o); }}
      onOpenChangeComplete={(o) => { if (!o) { setStep("claims"); setLeaving(null); } }}>
      <SheetContent ref={popup} side="bottom" showCloseButton={!submitting}
        className={`gap-0 overflow-hidden p-0 ${forward ? "max-h-dvh rounded-none data-[side=bottom]:h-dvh" : "max-h-[85dvh] rounded-t-xl"}`}>
        <SheetDescription className="sr-only">The week&apos;s claim groups: drag the handles to reorder or to move a player to another group, then review and send.</SheetDescription>
        <div className="relative grid min-h-0 flex-auto grid-rows-1" style={{ "--stage-from": forward ? "1.25rem" : "-0.75rem" } as CSSProperties}>
          {(step === "claims" || leaving === "claims") && (
            // no bottom padding: the pinned action bar carries the inset
            <div key="claims" inert={step !== "claims"} className={`min-h-0 overflow-y-auto overscroll-contain px-4 pt-4 ${paneClass("claims")}`}>
              <PanelBody sheet onReview={() => go("review")}
                heading={(t) => (step === "claims" ? <SheetTitle className={LABEL}>{t}</SheetTitle> : <h2 className={LABEL}>{t}</h2>)} />
            </div>
          )}
          {(step === "review" || leaving === "review") && (
            <div key="review" inert={step !== "review"} className={`flex min-h-0 flex-col gap-4 px-4 pt-4 ${paneClass("review")}`}>
              <ReviewBody phone titled={step === "review"} onBack={() => go("claims")} onClose={() => onOpenChange(false)} />
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

// The week's claim groups: a column docked beside the table from `lg`, a bottom sheet behind a
// fixed bar below it. The column opens the Submit dialog; the sheet turns into the review itself.
export function ClaimsPanel() {
  const { data, groups } = useClaims();
  const [sheet, setSheet] = useState(false);
  const [review, setReview] = useState(false);
  const n = data.claims.length + data.now.length;
  return (
    <>
      <aside aria-label="Claims" className="hidden lg:sticky lg:top-16 lg:block lg:max-h-[calc(100dvh-5rem)] lg:overflow-y-auto lg:overscroll-contain lg:pb-4">
        <PanelBody heading={(t) => <h2 className={LABEL}>{t}</h2>} onReview={() => setReview(true)} />
      </aside>
      <div className={`fixed inset-x-0 bottom-0 z-20 border-t border-foreground/10 bg-card/95 px-4 pt-2 shadow-[0_-8px_24px_-16px_rgb(0_0_0_/_0.5)] lg:hidden ${PHONE_INSET}`}>
        <Button variant="ghost" className="h-12 w-full justify-between px-3" onClick={() => setSheet(true)}>
          <span className="font-display text-base font-semibold uppercase tracking-[0.1em]">
            Claims
            <span className="text-muted-foreground"> · <Swap>{groups.length ? `${groups.length} group${groups.length > 1 ? "s" : ""} · ${n} move${n === 1 ? "" : "s"}` : "none yet"}</Swap></span>
          </span>
          <ChevronUp aria-hidden className="size-5" />
        </Button>
      </div>
      <ClaimsSheet open={sheet} onOpenChange={setSheet} />
      <ClaimsSubmit open={review} onOpenChange={setReview} />
    </>
  );
}
