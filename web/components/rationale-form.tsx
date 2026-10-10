"use client";

import { LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { clientGet, clientPost, ClientError } from "@/app/api-client";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import type { Decision, Presets } from "@/lib/api";
import { formError } from "@/lib/decisions";
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
export function RationaleForm({ decision, presets, withOutcome = false, onSaved, onLater }: {
  decision: Pick<Decision, "id" | "kind"> & Partial<Pick<Decision, "rationale" | "rationale_note" | "outcome">>;
  presets: Presets;
  withOutcome?: boolean;
  onSaved: (d: Decision) => void;
  onLater?: () => void;
}) {
  const options = presets[decision.kind] ?? [];
  const [key, setKey] = useState<string | null>(decision.rationale?.[0] ?? null);
  const [note, setNote] = useState(decision.rationale_note ?? "");
  const [outcome, setOutcome] = useState(decision.outcome ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    const bad = formError(key, note);
    if (bad) return setError(bad);
    setSaving(true);
    setError(null);
    try {
      onSaved(await clientPost<Decision>(`/decisions/${decision.id}`, { rationale: key ? [key] : null, note, ...(withOutcome ? { outcome } : {}) }));
    } catch (e) {
      setError(e instanceof ClientError ? e.message : "Could not save");
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
        <Label htmlFor={`note-${decision.id}`}>{key === "other" ? "Note" : "Add a note (optional)"}</Label>
        <Textarea id={`note-${decision.id}`} value={note} onChange={(e) => { setNote(e.target.value); setError(null); }} />
      </div>
      {withOutcome && (
        <div className="space-y-1.5">
          <Label htmlFor={`outcome-${decision.id}`}>Outcome</Label>
          <Textarea id={`outcome-${decision.id}`} value={outcome} onChange={(e) => setOutcome(e.target.value)} />
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
