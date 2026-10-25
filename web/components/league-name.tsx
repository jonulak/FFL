"use client";

import { useState } from "react";
import { clientPost } from "@/app/api-client";
import { useRouter } from "@/components/league-link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Swap } from "./motion";

const NAME_MAX = 60; // ffl/leagues.py NAME_MAX

// The league's name as the app shows it: the switcher, the pushes, these pages. ESPN's until it is renamed here; a
// save refreshes the layout so the switcher reads the new one at once.
export function LeagueName({ name: initial }: { name: string }) {
  const router = useRouter();
  const [saved, setSaved] = useState(initial);
  const [name, setName] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const clean = name.trim().replace(/\s+/g, " ");
  const changed = clean !== saved;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!clean || !changed) return;
    setBusy(true); setMsg(null);
    try {
      const out = await clientPost<{ key: string; name: string }>("/leagues/name", { name: clean });
      setSaved(out.name); setName(out.name);
      router.refresh();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    } finally { setBusy(false); }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Name</CardTitle>
        <CardDescription>How the switcher and the pushes name this league.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={save} className="flex flex-wrap items-center gap-2">
          <Input aria-label="League name" value={name} maxLength={NAME_MAX} disabled={busy}
                 onChange={(e) => setName(e.target.value)} className="min-w-0 flex-1 basis-56" />
          <Button type="submit" disabled={busy || !clean || !changed}><Swap>{busy ? "Saving…" : "Rename"}</Swap></Button>
        </form>
        {msg && <p key={msg} role="status" className="log-in mt-2 text-sm">{msg}</p>}
      </CardContent>
    </Card>
  );
}
