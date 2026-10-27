"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "./motion";

// Dev sign-in (FFL_DEV_SIGNIN, local and tailnet test servers only; POST /auth/dev, 404 when it is off): an email the API
// maps to a user, then a full page load to `next`, so the app's layout reads the new session from the start.
export function DevSignIn({ next }: { next: string }) {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/auth/dev", {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email: email.trim() }),
      });
      if (r.ok) {
        window.location.assign(next);
        return;
      }
      setError(r.status === 404 ? "Dev sign-in is off on this server." : `Sign-in refused (${r.status}).`);
    } catch {
      setError("Could not reach the server. Try again.");
    }
    setBusy(false);
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <Field data-invalid={error ? true : undefined}>
        <FieldLabel htmlFor="dev-email">Dev sign-in</FieldLabel>
        <Input id="dev-email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)}
          aria-invalid={error ? true : undefined} className="h-12 text-base" />
        {error ? <FieldError>{error}</FieldError> : <FieldDescription>This test server only.</FieldDescription>}
      </Field>
      <Button type="submit" variant="outline" disabled={busy || !email.trim()} className="h-12 w-full text-base">
        <Spinner on={busy} />Sign in
      </Button>
    </form>
  );
}
