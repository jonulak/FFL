"use client";

import { useState } from "react";
import { toast } from "sonner";
import { ClientError, clientPost } from "@/app/api-client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { Account, AccountSession } from "@/lib/api";
import { errorCode, SIGNIN_PATH } from "@/lib/auth";
import { deviceLabel } from "@/lib/devices";
import { PHONE_ACTION } from "./claims-submit";
import { Ago } from "./local-time";
import { Spinner } from "./motion";

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

// The account's own settings (ffl-soc.6.2.4), every league's and none's: who is signed in, their password, every device
// signed in (each signed out from here), and deleting the account. On /settings and on /welcome (a user with no league
// never reaches /settings: its device card reads a league).
export function AccountSettings({ account }: { account: Account }) {
  const [sessions, setSessions] = useState(account.sessions);
  const others = sessions.filter((s) => !s.current);
  const [busy, setBusy] = useState<string | null>(null);  // the session id (or "others") being signed out

  async function revoke(s: AccountSession) {
    setBusy(s.id);
    try {
      await clientPost(`/auth/sessions/${s.id}/revoke`);
      setSessions((all) => all.filter((x) => x.id !== s.id));
    } catch (e) {
      toast.error(`Could not sign out ${deviceLabel(s.ua)}: ${errText(e)}`);
    } finally {
      setBusy(null);
    }
  }

  async function revokeOthers() {
    setBusy("others");
    try {
      await clientPost("/auth/signout-others");
      setSessions((all) => all.filter((x) => x.current));
    } catch (e) {
      toast.error(`Could not sign out the other devices: ${errText(e)}`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader><CardTitle>Account</CardTitle></CardHeader>
        <CardContent className="space-y-3 text-sm">
          <div className="min-w-0">
            <p className="break-words font-medium">{account.username}{account.is_admin && <span className="font-normal text-muted-foreground"> · admin</span>}</p>
            {(account.name || account.email) && (
              <p className="break-words text-muted-foreground">{[account.name, account.email].filter(Boolean).join(" · ")}</p>
            )}
          </div>
          <ChangePassword />
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Signed in on</CardTitle></CardHeader>
        <CardContent className="text-sm">
          <ul className="divide-y divide-foreground/10">
            {sessions.map((s) => (
              <li key={s.id} className="flex min-h-12 items-center justify-between gap-3 py-2 first:pt-0 last:pb-0">
                <div className="min-w-0">
                  <p className="truncate font-medium">{deviceLabel(s.ua)}</p>
                  <p className="text-xs text-muted-foreground">
                    {s.current ? "This device" : <>Active <Ago iso={s.last_seen} /></>}
                  </p>
                </div>
                {!s.current && (
                  <Button variant="ghost" size="sm" onClick={() => revoke(s)} disabled={busy !== null}
                          aria-label={`Sign out ${deviceLabel(s.ua)}`} className="shrink-0 max-lg:h-10 max-lg:px-3">
                    <Spinner on={busy === s.id} />Sign out
                  </Button>
                )}
              </li>
            ))}
          </ul>
          {others.length > 1 && (
            <Button variant="outline" onClick={revokeOthers} disabled={busy !== null} className={`mt-3 ${PHONE_ACTION}`}>
              <Spinner on={busy === "others"} />Sign out the other {others.length}
            </Button>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// A new password, with the current one (POST /auth/password): this session and the others stay signed in. Its 401
// bad_login is the wrong current password, never signed out (lib/auth.ts signedOutRedirect keys on signed_out).
function ChangePassword() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setMsg(null);
    try {
      await clientPost("/auth/password", { current, new: next });
      setCurrent(""); setNext("");
      setMsg({ ok: true, text: "Password changed." });
    } catch (err) {
      const code = err instanceof ClientError ? errorCode(err.body) : null;
      setMsg({ ok: false, text: code === "bad_login" ? "That isn't your current password."
        : code === "short_password" ? "Use at least 8 characters for the new password." : `Not changed: ${errText(err)}` });
    }
    setBusy(false);
  }

  const invalid = msg && !msg.ok ? true : undefined;
  return (
    <form onSubmit={save} className="space-y-3 border-t border-foreground/10 pt-3">
      <FieldGroup className="gap-3">
        <Field data-invalid={invalid}>
          <FieldLabel htmlFor="pw-current">Current password</FieldLabel>
          <Input id="pw-current" type="password" autoComplete="current-password" required value={current}
                 onChange={(e) => setCurrent(e.target.value)} aria-invalid={invalid} className="max-lg:h-12 max-lg:text-base" />
        </Field>
        <Field data-invalid={invalid}>
          <FieldLabel htmlFor="pw-new">New password</FieldLabel>
          <Input id="pw-new" type="password" autoComplete="new-password" required minLength={8} value={next}
                 onChange={(e) => setNext(e.target.value)} aria-invalid={invalid} className="max-lg:h-12 max-lg:text-base" />
        </Field>
        {msg && (msg.ok ? <p role="status" className="log-in">{msg.text}</p> : <FieldError>{msg.text}</FieldError>)}
      </FieldGroup>
      <Button type="submit" variant="outline" disabled={busy || !current || !next} className={PHONE_ACTION}>
        <Spinner on={busy} />Change password
      </Button>
    </form>
  );
}

// Deleting the account: every personal row (decisions, watchlist, notices, devices, settings) and every sign-in, for
// good. A league and what its other members share stay. Typing the username is the confirmation the API checks too.
export function DeleteAccount({ username }: { username: string }) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const match = username !== "" && typed.trim().toLowerCase() === username.toLowerCase();

  async function remove() {
    setBusy(true); setMsg(null);
    try {
      await clientPost("/auth/delete", { username: typed });
      window.location.assign(SIGNIN_PATH);
    } catch (e) {
      const code = e instanceof ClientError ? errorCode(e.body) : null;
      setMsg(code === "last_admin" ? "Not deleted: you are the only admin. Make another user an admin first."
        : `Not deleted: ${errText(e)}`);
      setBusy(false);
    }
  }

  return (
    <section className="space-y-2">
      <h2 className="text-base font-semibold">Delete account</h2>
      <p className="text-sm text-muted-foreground">
        Removes your decisions, watchlist, notifications, devices and settings, and signs you out everywhere. Your
        leagues stay for their other members.
      </p>
      <Button variant="destructive" onClick={() => { setTyped(""); setMsg(null); setOpen(true); }} className={PHONE_ACTION}>
        Delete account
      </Button>
      <Dialog open={open} onOpenChange={(o) => !busy && setOpen(o)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete your account?</DialogTitle>
            <DialogDescription>This can&apos;t be undone. Type {username} to confirm.</DialogDescription>
          </DialogHeader>
          <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); if (match && !busy) remove(); }}>
            <Label htmlFor="delete-username" className="sr-only">Your username</Label>
            <Input id="delete-username" autoComplete="off" autoCapitalize="none" spellCheck={false}
                   value={typed} onChange={(e) => setTyped(e.target.value)} disabled={busy} />
            {msg && <p role="status" className="log-in text-sm text-destructive">{msg}</p>}
          </form>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setOpen(false)} disabled={busy} className={PHONE_ACTION}>Cancel</Button>
            <Button variant="destructive" onClick={remove} disabled={!match || busy} className={PHONE_ACTION}>
              <Spinner on={busy} />Delete for good
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
