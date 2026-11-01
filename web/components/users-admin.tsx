"use client";

import { useState } from "react";
import { toast } from "sonner";
import { ClientError, clientGet, clientPost } from "@/app/api-client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import type { AdminUser } from "@/lib/api";
import { errorCode } from "@/lib/auth";
import { PHONE_ACTION } from "./claims-submit";
import { Spinner } from "./motion";

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const label = (u: AdminUser) => u.username ?? u.name ?? u.email ?? `User ${u.id}`;

// Settings › Users (admins only; ffl/auth.py's /admin/users): everyone on this server, an invite (a user with no login
// yet and the link that sets one), a new set-password link for anyone, the admin switch, and deleting another user.
// A link is shown once, in a dialog with a copy button: the server keeps only its hash.
export function UsersAdmin({ initial, me }: { initial: AdminUser[]; me: string | null }) {
  const [users, setUsers] = useState(initial);
  const [busy, setBusy] = useState<string | null>(null);  // "invite", or `<action>:<id>`
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [link, setLink] = useState<{ title: string; url: string } | null>(null);
  const [doomed, setDoomed] = useState<AdminUser | null>(null);

  const reload = async () => setUsers((await clientGet<{ users: AdminUser[] }>("/admin/users")).users);

  async function invite(e: React.FormEvent) {
    e.preventDefault();
    setBusy("invite");
    try {
      const r = await clientPost<{ link: string }>("/admin/users", { name: name.trim() || null, email: email.trim() || null });
      setLink({ title: `Invite link${name.trim() ? ` for ${name.trim()}` : ""}`, url: r.link });
      setName(""); setEmail("");
      reload().catch(() => toast.error("Invited. Reload the page to see them in the list."));
    } catch (err) {
      toast.error(err instanceof ClientError && errorCode(err.body) === "email_taken"
        ? "Someone on this server has that email already." : `Not invited: ${errText(err)}`);
    } finally {
      setBusy(null);
    }
  }

  async function reset(u: AdminUser) {
    setBusy(`reset:${u.id}`);
    try {
      const r = await clientPost<{ link: string }>(`/admin/users/${u.id}/reset`);
      setLink({ title: `Set-password link for ${label(u)}`, url: r.link });
    } catch (err) {
      toast.error(`No link made: ${errText(err)}`);
    } finally {
      setBusy(null);
    }
  }

  async function setAdmin(u: AdminUser, is_admin: boolean) {
    setBusy(`admin:${u.id}`);
    try {
      await clientPost(`/admin/users/${u.id}/admin`, { is_admin });
      setUsers((all) => all.map((x) => (x.id === u.id ? { ...x, is_admin } : x)));
    } catch (err) {
      toast.error(err instanceof ClientError && errorCode(err.body) === "last_admin"
        ? "The server needs an admin: make someone else one first." : `Not changed: ${errText(err)}`);
    } finally {
      setBusy(null);
    }
  }

  async function remove(u: AdminUser) {
    setBusy(`delete:${u.id}`);
    try {
      await clientPost(`/admin/users/${u.id}/delete`);
      setUsers((all) => all.filter((x) => x.id !== u.id));
      setDoomed(null);
    } catch (err) {
      toast.error(`${label(u)} not deleted: ${errText(err)}`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <CardHeader><CardTitle>Users</CardTitle></CardHeader>
      <CardContent className="space-y-4 text-sm">
        <ul className="divide-y divide-foreground/10">
          {users.map((u) => {
            const self = u.username !== null && u.username === me;
            const detail = [u.username ? u.name : null, u.email].filter(Boolean).join(" · ");
            return (
              <li key={u.id} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 py-3 first:pt-0 last:pb-0">
                <div className="min-w-0">
                  <p className="break-words font-medium">{label(u)}{self && <span className="font-normal text-muted-foreground"> · you</span>}</p>
                  <p className="break-words text-xs text-muted-foreground">
                    {[detail, u.has_password ? null : "Invited, not set up yet"].filter(Boolean).join(" · ") || " "}
                  </p>
                </div>
                <div className="flex items-center gap-1">
                  <Label className="flex min-h-10 items-center gap-2 px-2 font-normal">
                    <Switch checked={u.is_admin} disabled={busy !== null} onCheckedChange={(v) => setAdmin(u, v)} />Admin
                  </Label>
                  <Button variant="ghost" size="sm" onClick={() => reset(u)} disabled={busy !== null}
                          aria-label={`New set-password link for ${label(u)}`} className="max-lg:h-10 max-lg:px-3">
                    <Spinner on={busy === `reset:${u.id}`} />New link
                  </Button>
                  {!self && (
                    <Button variant="ghost" size="sm" onClick={() => setDoomed(u)} disabled={busy !== null}
                            aria-label={`Delete ${label(u)}`} className="text-destructive max-lg:h-10 max-lg:px-3">
                      Delete
                    </Button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>

        <form onSubmit={invite} className="space-y-3 border-t border-foreground/10 pt-4">
          <p className="font-medium">Invite someone</p>
          <FieldGroup className="gap-3 sm:flex-row">
            <Field>
              <FieldLabel htmlFor="invite-name">Name (optional)</FieldLabel>
              <Input id="invite-name" autoComplete="off" maxLength={100} value={name} onChange={(e) => setName(e.target.value)}
                     className="max-lg:h-12 max-lg:text-base" />
            </Field>
            <Field>
              <FieldLabel htmlFor="invite-email">Email (optional)</FieldLabel>
              <Input id="invite-email" type="email" autoComplete="off" autoCapitalize="none" spellCheck={false} maxLength={254}
                     value={email} onChange={(e) => setEmail(e.target.value)} className="max-lg:h-12 max-lg:text-base" />
            </Field>
          </FieldGroup>
          <Button type="submit" variant="outline" disabled={busy !== null} className={PHONE_ACTION}>
            <Spinner on={busy === "invite"} />Make invite link
          </Button>
        </form>
      </CardContent>

      <LinkDialog link={link} onClose={() => setLink(null)} />

      <Dialog open={doomed !== null} onOpenChange={(o) => !o && busy === null && setDoomed(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {doomed && label(doomed)}?</DialogTitle>
            <DialogDescription>
              Removes their decisions, watchlist, notifications, devices and settings, and signs them out everywhere.
              Their leagues stay for the other members. This can&apos;t be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setDoomed(null)} disabled={busy !== null} className={PHONE_ACTION}>Cancel</Button>
            <Button variant="destructive" onClick={() => doomed && remove(doomed)} disabled={busy !== null} className={PHONE_ACTION}>
              <Spinner on={busy === `delete:${doomed?.id}`} />Delete for good
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

// The link, once: selectable in a read-only field, with a copy button (no clipboard on a plain-http server: the field
// is selected instead, to copy by hand).
function LinkDialog({ link, onClose }: { link: { title: string; url: string } | null; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(link!.url);
      setCopied(true);
    } catch {
      (document.getElementById("setup-link") as HTMLInputElement | null)?.select();
      toast("Couldn't copy here: the link is selected, copy it by hand.");
    }
  }
  return (
    <Dialog open={link !== null} onOpenChange={(o) => { if (!o) { setCopied(false); onClose(); } }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{link?.title}</DialogTitle>
          <DialogDescription>
            Send it to them: it sets their username and password, works once, and expires in 7 days. It won&apos;t be shown again.
          </DialogDescription>
        </DialogHeader>
        <Label htmlFor="setup-link" className="sr-only">Link</Label>
        <Input id="setup-link" readOnly value={link?.url ?? ""} onFocus={(e) => e.currentTarget.select()}
               className="font-mono text-xs max-lg:h-12" />
        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={() => { setCopied(false); onClose(); }} className={PHONE_ACTION}>Done</Button>
          <Button onClick={copy} className={PHONE_ACTION}>{copied ? "Copied" : "Copy link"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
