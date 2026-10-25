"use client";

import { useState } from "react";
import { clientPost } from "@/app/api-client";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { parseCap, type Caps } from "@/lib/caps";
import { Spinner } from "./motion";

// The open league's roster caps (ffl-soc.4.10): the league's limit, my cap (saved on leaving the field, like the
// quiet hours) and the cap the draft tools hold me to, the lower of the two. A bad count says why and puts the saved
// one back.
export function CapsSettings({ caps }: { caps: Caps }) {
  const [rows, setRows] = useState(caps.rows);
  const [draft, setDraft] = useState<Record<string, string>>(() => Object.fromEntries(caps.rows.map((r) => [r.pos, r.my_cap == null ? "" : String(r.my_cap)])));
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  async function save(pos: string) {
    const row = rows.find((r) => r.pos === pos)!;
    const text = draft[pos] ?? "";
    if (text.trim() === (row.my_cap == null ? "" : String(row.my_cap))) return;
    const back = () => setDraft((d) => ({ ...d, [pos]: row.my_cap == null ? "" : String(row.my_cap) }));
    const parsed = parseCap(text, row.provider_limit);
    if ("error" in parsed) { setMsg(`${pos}: ${parsed.error}`); back(); return; }
    setBusy(pos); setMsg(null);
    try {
      const out = await clientPost<Caps>("/caps", { pos, my_cap: parsed.ok });
      setRows(out.rows);
      setDraft((d) => ({ ...d, [pos]: String(parsed.ok) }));
    } catch (e) {
      setMsg(`${pos}: ${e instanceof Error ? e.message : String(e)}`); back();
    } finally { setBusy(null); }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Roster caps</CardTitle>
        <CardDescription>How many of each position the draft tools let me take in {caps.name}. Never above the league&apos;s limit.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <Table className="tabular-nums">
          <TableHeader>
            <TableRow>
              <TableHead>Pos</TableHead>
              <TableHead className="text-right">League</TableHead>
              <TableHead className="text-right">Mine</TableHead>
              <TableHead className="text-right">Cap</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.pos}>
                <TableCell className="font-display font-semibold uppercase tracking-[0.08em]">{r.pos}</TableCell>
                <TableCell className="text-right text-muted-foreground">{r.provider_limit ?? "—"}</TableCell>
                <TableCell className="text-right">
                  <span className="inline-flex items-center justify-end gap-1.5">
                    <Spinner on={busy === r.pos} />
                    <Input inputMode="numeric" pattern="[0-9]*" aria-label={`My ${r.pos} cap`} className="h-9 w-16 text-right tabular-nums"
                           value={draft[r.pos] ?? ""} placeholder="—" disabled={busy != null}
                           onChange={(e) => setDraft((d) => ({ ...d, [r.pos]: e.target.value }))}
                           onBlur={() => save(r.pos)}
                           onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }} />
                  </span>
                </TableCell>
                <TableCell className="text-right font-medium">{r.cap ?? "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {msg && <p key={msg} role="status" className="log-in text-sm">{msg}</p>}
      </CardContent>
    </Card>
  );
}
