"use client";

import { useState } from "react";
import { toast } from "sonner";
import { clientPost } from "@/app/api-client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { Account, AccountSession } from "@/lib/api";
import { SIGNIN_PATH } from "@/lib/auth";
import { deviceLabel } from "@/lib/devices";
import { PHONE_ACTION } from "./claims-submit";
import { Ago } from "./local-time";
import { Spinner } from "./motion";

const PROVIDER: Record<string, string> = { google: "Google", apple: "Apple" };

// What a ?link= answer from the Google round trip says (ffl/auth.py `_link_result`)
const LINKED: Record<string, string> = {
  linked: "Linked. You can sign in with either now.",
  taken: "That sign-in belongs to another FFL account, so it was not linked.",
  failed: "Linking did not finish. Try again.",
};

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

// The account's own settings (ffl-soc.6.2.4), every league's and none's: who is signed in and with what, every device
// signed in (each signed out from here), and deleting the account. On /settings and on /welcome (a user with no league
// never reaches /settings: its device card reads a league).
export function AccountSettings({ account, link }: { account: Account; link?: string }) {
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
            <p className="truncate font-medium">{account.email}</p>
            {account.name && <p className="truncate text-muted-foreground">{account.name}</p>}
          </div>
          <ul className="space-y-1">
            {account.identities.map((i) => (
              <li key={`${i.provider}:${i.email}`} className="flex min-w-0 gap-1.5">
                <span className="shrink-0 text-muted-foreground">Signs in with</span>
                <span className="truncate">{PROVIDER[i.provider] ?? i.provider}{i.email && i.email.toLowerCase() !== account.email.toLowerCase() ? ` · ${i.email}` : ""}</span>
              </li>
            ))}
          </ul>
          {account.linkable.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {/* a full load: the round trip leaves for the provider and comes back to /settings?link= */}
              {account.linkable.map((p) => (
                <Button key={p} variant="outline" className={PHONE_ACTION}
                        render={<a href={`/api/auth/${p}/link`} />} nativeButton={false}>
                  Link {PROVIDER[p] ?? p}
                </Button>
              ))}
            </div>
          )}
          {link && LINKED[link] && <p role="status" className="log-in">{LINKED[link]}</p>}
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

// Deleting the account: every personal row (decisions, watchlist, notices, devices, settings) and every sign-in, for
// good. A league and what its other members share stay. Typing the email is the confirmation the API checks too.
export function DeleteAccount({ email }: { email: string }) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const match = typed.trim().toLowerCase() === email.toLowerCase();

  async function remove() {
    setBusy(true); setMsg(null);
    try {
      await clientPost("/auth/delete", { email: typed });
      window.location.assign(SIGNIN_PATH);
    } catch (e) {
      setMsg(`Not deleted: ${errText(e)}`);
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
            <DialogDescription>This can&apos;t be undone. Type {email} to confirm.</DialogDescription>
          </DialogHeader>
          <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); if (match && !busy) remove(); }}>
            <Label htmlFor="delete-email" className="sr-only">Your email</Label>
            <Input id="delete-email" type="email" autoComplete="off" autoCapitalize="none" spellCheck={false}
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
