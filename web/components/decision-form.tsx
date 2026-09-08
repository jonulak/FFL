"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { clientPost } from "@/app/api-client";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

const FIELDS = [
  ["title", "Title", false],
  ["decision", "Decision", true],
  ["reasoning", "Reasoning", true],
  ["alternatives", "Alternatives rejected", true],
] as const;

type Field = (typeof FIELDS)[number][0];
const EMPTY: Record<Field, string> = { title: "", decision: "", reasoning: "", alternatives: "" };

export function DecisionForm() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [v, setV] = useState<Record<Field, string>>(EMPTY);

  function set(k: Field, value: string) {
    setV((prev) => ({ ...prev, [k]: value }));
  }

  // On failure keep the dialog open with the typed values intact: the entry is a paragraph of
  // reasoning, and losing it to a 422 would be worse than the rejection itself.
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await clientPost("/decisions", v);
      setV(EMPTY);
      setErr(null);
      setOpen(false);
      router.refresh();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : String(ex));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button size="sm" />}>Log a decision</DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>New DECISIONS.md entry</DialogTitle>
          <DialogDescription>
            Appended to journal/DECISIONS.md. Title, decision and reasoning are required.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-3">
          {FIELDS.map(([k, label, multi]) => (
            <div key={k} className="space-y-1">
              <Label htmlFor={k}>{label}</Label>
              {multi ? (
                <Textarea
                  id={k}
                  name={k}
                  value={v[k]}
                  rows={3}
                  onChange={(e) => set(k, e.target.value)}
                />
              ) : (
                <Input id={k} name={k} value={v[k]} onChange={(e) => set(k, e.target.value)} />
              )}
            </div>
          ))}
          {err && (
            <p role="alert" className="text-sm break-words text-destructive">
              {err}
            </p>
          )}
          <div className="flex justify-end">
            <Button type="submit" disabled={busy}>
              {busy ? "Saving…" : "Append"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
