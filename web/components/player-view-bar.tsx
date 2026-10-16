"use client";

import { ChevronDown, SlidersHorizontal } from "lucide-react";
import { useRef, useState } from "react";
import { clientPost, ClientError } from "@/app/api-client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import type { PresetStore } from "@/lib/api";
import { filterLabel } from "@/lib/player-view";
import { presetView } from "@/lib/view-draft";
import { usePlayerView } from "./player-view-context";
import { PlayerViewSheet, type SheetFocus } from "./player-view-sheet";

// The line over the Players table (ffl-8iz): which preset the view starts from, whether it has been
// changed since (Save writes the change into it, Save as makes a new one, Reset drops it), and the View
// sheet that changes it.
const CAPS = "font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em]";
const CTRL = "h-10 rounded-md border-foreground/15 sm:h-8";

export function PlayerViewBar() {
  const { presetId, preset, presets, defaultId, view, modified, pending, go } = usePlayerView();
  const [open, setOpen] = useState(false);
  const [focus, setFocus] = useState<SheetFocus>("view");
  const [saveAs, setSaveAs] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const off = busy || pending;

  // a refused POST says why under the bar and changes nothing here
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      return true;
    } catch (e) {
      setError(e instanceof ClientError ? e.message : String(e));
      return false;
    } finally {
      setBusy(false);
    }
  };
  // the provider's copy of the preset is the old one until the page re-reads: diff against what was saved
  const save = () => run(async () => {
    await clientPost<PresetStore>(`/players/presets/${presetId}`, view);
    go(view, { base: view });
  });
  const create = (name: string) => run(async () => {
    const r = await clientPost<PresetStore & { id: string }>("/players/presets", { name, ...view });
    go(view, { presetId: r.id, base: view });
  });
  const sheet = (f: SheetFocus) => { setFocus(f); setOpen(true); };

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <DropdownMenu>
          <DropdownMenuTrigger disabled={off} render={<Button variant="outline" className={`${CTRL} max-w-[60vw] gap-1.5 px-3 sm:max-w-none ${CAPS}`} />}>
            <span className="truncate">{preset.name}</span>
            {presetId === defaultId && <span aria-label="(the default)" title="the default view" className="size-1.5 shrink-0 rounded-full bg-foreground/55" />}
            <ChevronDown aria-hidden className="text-muted-foreground" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-auto min-w-56">
            <DropdownMenuRadioGroup value={presetId} onValueChange={(id: string) => {
              const p = presets.find((x) => x.id === id);
              if (p) go(presetView(p), { presetId: p.id });
            }}>
              {presets.map((p) => (
                <DropdownMenuRadioItem key={p.id} value={p.id} closeOnClick className="min-h-10 gap-3 sm:min-h-8">
                  <span className="truncate">{p.name}</span>
                  {p.id === defaultId && <span className="ml-auto text-xs text-muted-foreground">default</span>}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => sheet("presets")} className="min-h-10 sm:min-h-8">Manage presets…</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        {/* a phone keeps the preset and View on the first line and puts the changes' actions on their own */}
        {modified && (
          <div className="order-last flex w-full items-center gap-1.5 sm:order-none sm:w-auto">
            <Badge variant="muted" shape="chip" title={`changed from the saved ${preset.name}`} className="mr-1">modified</Badge>
            <Button variant="outline" size="sm" onClick={save} disabled={off} className={`${CTRL} px-3`}>Save</Button>
            <Button variant="ghost" size="sm" onClick={() => setSaveAs(true)} disabled={off} className="h-10 px-3 sm:h-8">Save as</Button>
            <Button variant="ghost" size="sm" onClick={() => go(presetView(preset))} disabled={off} className="h-10 px-3 text-muted-foreground sm:h-8">Reset</Button>
          </div>
        )}
        <Button variant="outline" onClick={() => sheet("view")} disabled={off} aria-label={view.filters.length ? `View: ${view.filters.length} filter${view.filters.length === 1 ? "" : "s"} on` : "View"}
          title={view.filters.map(filterLabel).join(" · ") || undefined}
          className={`${CTRL} ml-auto gap-1.5 px-3 ${CAPS}`}>
          <SlidersHorizontal aria-hidden />
          View
          {view.filters.length > 0 && <Badge className="ml-0.5 h-4.5 min-w-4.5 px-1 tabular-nums tracking-normal">{view.filters.length}</Badge>}
        </Button>
      </div>
      {view.filters.length > 0 && (
        <p className="text-xs text-muted-foreground">
          <span className="sr-only">filters: </span>{view.filters.map(filterLabel).join(" · ")}
        </p>
      )}
      {error && !saveAs && <p role="alert" className="text-xs text-destructive">{error}</p>}
      <PlayerViewSheet open={open} onOpenChange={setOpen} focus={focus} />
      <SaveAsDialog open={saveAs} onOpenChange={setSaveAs} placeholder={`${preset.name} 2`} disabled={off}
        onSave={async (name) => { if (await create(name)) setSaveAs(false); }} error={error} />
    </div>
  );
}

function SaveAsDialog({ open, onOpenChange, placeholder, disabled, onSave, error }: {
  open: boolean; onOpenChange: (o: boolean) => void; placeholder: string; disabled: boolean; onSave: (name: string) => void; error: string | null;
}) {
  const input = useRef<HTMLInputElement | null>(null);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent initialFocus={input} className="sm:max-w-sm">
        <form className="grid gap-4" onSubmit={(e) => {
          e.preventDefault();
          onSave(input.current?.value.trim() || placeholder);
        }}>
          <div className="grid gap-1">
            <DialogTitle className="font-display text-xl font-bold tracking-tight">Save as a new preset</DialogTitle>
            <DialogDescription className="text-xs">The columns, filters, sort, position and Free agents toggle on screen now.</DialogDescription>
          </div>
          <Input ref={input} name="name" maxLength={40} placeholder={placeholder} aria-label="preset name" autoComplete="off"
            className="h-10 border-foreground/15 text-base sm:text-sm" />
          {open && error && <p role="alert" className="-mt-2 text-xs text-destructive">{error}</p>}
          <DialogFooter className="gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} className="h-10 border-foreground/15 sm:h-8">Cancel</Button>
            <Button type="submit" disabled={disabled} className="h-10 min-w-20 sm:h-8">Save</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
