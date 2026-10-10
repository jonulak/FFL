"use client";

import { LoaderCircle } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { clientGet, clientPost } from "@/app/api-client";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import type { Decision, Presets } from "@/lib/api";
import { changedReason, formError } from "@/lib/decisions";
import { PHONE_ACTION } from "./claims-submit";

// The reasons offered per kind, asked once per page load and shared by every form on it.
let presetsLoad: Promise<Presets> | null = null;

export function usePresets(): Presets | null {
  const [presets, setPresets] = useState<Presets | null>(null);
  useEffect(() => {
    let current = true;
    presetsLoad ??= clientGet<Presets>("/decisions/presets");
    // a failed ask is dropped so the next form asks again; without presets the dialog stays shut
    presetsLoad.then((p) => current && setPresets(p), () => { presetsLoad = null; });
    return () => { current = false; };
  }, []);
  return presets;
}

// Why a decision was made: one preset, a note, and (on the Decisions page's Edit) how it turned
// out. No dialog chrome, so the move sheets and the dialog can both hold it. A failed save keeps
// every field as typed and says why under the buttons.
// A move's prompt opens at the tap, before its row exists: `targets` then settles with the rows the
// answer recorded (the first takes the form, the rest the same reason), and a Save pressed sooner waits for it.
export function RationaleForm({ decision, targets, presets, withOutcome = false, onSaved, onLater }: {
  decision: Pick<Decision, "kind"> & Partial<Pick<Decision, "id" | "rationale" | "rationale_note" | "outcome">>;
  targets?: Promise<number[]>;
  presets: Presets;
  withOutcome?: boolean;
  onSaved: (d: Decision, failedCopies: number) => void;
  onLater?: () => void;
}) {
  const options = presets[decision.kind] ?? [];
  const uid = useId();
  const [key, setKey] = useState<string | null>(decision.rationale?.[0] ?? null);
  const [note, setNote] = useState(decision.rationale_note ?? "");
  const [outcome, setOutcome] = useState(decision.outcome ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    // only a changed reason is checked and sent: an outcome-only Edit of a stored reason saves as it stands
    const changed = changedReason(decision, key, note);
    const bad = changed || key == null ? formError(key, note, withOutcome ? outcome : undefined) : null;
    if (bad) return setError(bad);
    setSaving(true);
    setError(null);
    try {
      const [id, ...rest] = targets ? await targets : decision.id != null ? [decision.id] : [];
      if (id == null) throw new Error("Not recorded, so there is nothing to save it to");
      const body = { ...(changed ? { rationale: key ? [key] : null, note } : {}), ...(withOutcome ? { outcome } : {}) };
      const row = await clientPost<Decision>(`/decisions/${id}`, body);
      // the form's own row stands whatever happens to the copies; a failed copy stays open on the Decisions page
      const copies = await Promise.allSettled(rest.map((r) => clientPost(`/decisions/${r}`, body)));
      onSaved(row, copies.filter((c) => c.status === "rejected").length);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save");
      setSaving(false);
    }
  }

  return (
    <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <Select value={key} onValueChange={(v) => { setKey(v); setError(null); }}>
        <SelectTrigger aria-label="Reason" className="w-full">
          <SelectValue placeholder="Why?">{key ? options.find((o) => o.key === key)?.label ?? key : null}</SelectValue>
        </SelectTrigger>
        <SelectContent alignItemWithTrigger={false}>
          {options.map((o) => <SelectItem key={o.key} value={o.key}>{o.label}</SelectItem>)}
        </SelectContent>
      </Select>
      <div className="space-y-1.5">
        <Label htmlFor={`note-${uid}`}>{key === "other" ? "Note" : "Add a note (optional)"}</Label>
        <Textarea id={`note-${uid}`} value={note} onChange={(e) => { setNote(e.target.value); setError(null); }} />
      </div>
      {withOutcome && (
        <div className="space-y-1.5">
          <Label htmlFor={`outcome-${uid}`}>Outcome</Label>
          <Textarea id={`outcome-${uid}`} value={outcome} onChange={(e) => setOutcome(e.target.value)} />
        </div>
      )}
      <div className="flex gap-2">
        <Button type="submit" disabled={saving} className={PHONE_ACTION}>
          {saving && <LoaderCircle aria-hidden className="size-4 animate-spin" />}
          Save
        </Button>
        {onLater && <Button type="button" variant="outline" disabled={saving} onClick={onLater} className={PHONE_ACTION}>Later</Button>}
      </div>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    </form>
  );
}
