"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { SIGNIN_PATH } from "@/lib/auth";
import { PHONE_ACTION } from "./claims-submit";
import { Spinner } from "./motion";

// The Settings page's Sign out (ffl-soc.6.2.1): POST /auth/signout ends the session and expires the cookie, then a full
// page load to /signin, so nothing of the signed-in app (its layout, its cached pages) stays on screen.
export function SignOutButton() {
  const [busy, setBusy] = useState(false);
  async function signOut() {
    setBusy(true);
    try {
      const r = await fetch("/api/auth/signout", { method: "POST" });
      if (!r.ok && r.status !== 401) throw new Error(`${r.status} ${r.statusText}`);
      window.location.assign(SIGNIN_PATH);
    } catch (e) {
      toast.error(`Could not sign out: ${e instanceof Error ? e.message : String(e)}`);
      setBusy(false);
    }
  }
  return (
    <Button variant="outline" onClick={signOut} disabled={busy} className={PHONE_ACTION}>
      <Spinner on={busy} />Sign out
    </Button>
  );
}
