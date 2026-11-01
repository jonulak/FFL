"use client";

import { useState } from "react";
import { ClientError, clientGet, clientPost } from "@/app/api-client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import type { LlmToken } from "@/lib/api";
import { tokenError } from "@/lib/llm";
import { PHONE_ACTION } from "./claims-submit";
import { Spinner } from "./motion";

// The server's ping runs `claude -p` for up to 60 s before it answers.
const SAVE_TIMEOUT_MS = 75_000;

// The Claude login the outlooks and the news desk run under (admins: GET/POST /llm/token, /llm/token/delete).
// Save tests the token with one call and stores it only when that works; Remove drops it. On Settings › App, and as
// the wizard's first step on a fresh server.
export function LlmSettings({ initial, onSaved }: { initial: LlmToken; onSaved?: () => void }) {
  const [status, setStatus] = useState(initial);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState<"save" | "remove" | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy("save"); setMsg(null);
    try {
      setStatus({ ...(await clientPost<{ set: boolean }>("/llm/token", { token }, SAVE_TIMEOUT_MS)), source: "db" });
      setToken("");
      setMsg({ ok: true, text: "Saved. Claude answered with it." });
      onSaved?.();
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ClientError ? tokenError(err.body, err.message)
        : `Not saved: ${err instanceof Error ? err.message : String(err)}` });
    }
    setBusy(null);
  }

  async function remove() {
    setBusy("remove"); setMsg(null);
    try {
      await clientPost("/llm/token/delete");
      setStatus(await clientGet<LlmToken>("/llm/token"));
      setMsg({ ok: true, text: "Removed." });
    } catch (err) {
      setMsg({ ok: false, text: `Not removed: ${err instanceof Error ? err.message : String(err)}` });
    }
    setBusy(null);
  }

  const invalid = msg && !msg.ok ? true : undefined;
  return (
    <Card>
      <CardHeader><CardTitle>Claude</CardTitle></CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-muted-foreground">
          {status.source === "db" ? "Connected: outlooks and the news desk run on the token saved here."
            : "Not connected: outlooks and the news desk wait until a token is saved."}
        </p>
        <form onSubmit={save} className="space-y-3">
          <Field data-invalid={invalid}>
            <FieldLabel htmlFor="llm-token">{status.source === "db" ? "Replace the token" : "Claude token"}</FieldLabel>
            <Input id="llm-token" type="password" autoComplete="off" spellCheck={false} required value={token}
                   onChange={(e) => setToken(e.target.value)} aria-invalid={invalid} className="font-mono max-lg:h-12 max-lg:text-base" />
            <FieldDescription>
              Run <code className="font-mono text-foreground">claude setup-token</code> on a computer where you&apos;re signed in to
              Claude, and paste the token here. An Anthropic API key works too.
            </FieldDescription>
          </Field>
          {msg && (msg.ok ? <p role="status">{msg.text}</p> : <FieldError>{msg.text}</FieldError>)}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy !== null || !token.trim()} className={PHONE_ACTION}>
              <Spinner on={busy === "save"} />{busy === "save" ? "Testing…" : "Save"}
            </Button>
            {status.source === "db" && (
              <Button type="button" variant="outline" onClick={remove} disabled={busy !== null} className={PHONE_ACTION}>
                <Spinner on={busy === "remove"} />Remove
              </Button>
            )}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
