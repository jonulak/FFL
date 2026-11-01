"use client";

import { useState } from "react";
import { ClientError, clientPost } from "@/app/api-client";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { errorCode } from "@/lib/auth";
import { Spinner } from "./motion";

type Mode = "setup" | "signin" | "invite";

// ffl/auth.py's refusals, in the visitor's terms
const MESSAGES: Record<string, string> = {
  bad_login: "Wrong username or password.",
  rate_limited: "Too many wrong tries. Wait 15 minutes, then try again.",
  bad_username: "A username is 2 to 32 letters, numbers, dots, dashes or underscores.",
  short_password: "Use at least 8 characters for the password.",
  username_taken: "That username is taken. Pick another.",
  bad_token: "This link has expired or was already used. Ask your admin for a new one.",
};

// One username + password form for signing in, the first admin's setup (POST /setup) and an invite's or a reset's
// setup link (POST /auth/setup/<token>). Each answers with the session cookie; then a full page load to `next`, so the
// app's layout reads the new session from the start.
export function PasswordForm({ mode, next = "/", token }: { mode: Mode; next?: string; token?: string }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");  // setup and invite only: a typo in a new password has no way back
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const creating = mode !== "signin";
  const mismatch = creating && confirm !== "" && confirm !== password;
  const path = mode === "signin" ? "/auth/signin" : mode === "setup" ? "/setup" : `/auth/setup/${encodeURIComponent(token ?? "")}`;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await clientPost(path, { username: username.trim(), password });
      window.location.assign(next);
      return;
    } catch (err) {
      if (err instanceof ClientError) {
        const code = errorCode(err.body);
        setError((code && MESSAGES[code]) || (mode === "setup" && err.status === 404
          ? "This server is set up already. Sign in instead."
          : `Not signed in: ${err.message}`));
      } else {
        setError("Could not reach the server. Try again.");
      }
    }
    setBusy(false);
  }

  const invalid = error ? true : undefined;
  return (
    <form onSubmit={submit} className="flex flex-col gap-6">
      <FieldGroup className="gap-5">
        <Field data-invalid={invalid}>
          <FieldLabel htmlFor="username">Username</FieldLabel>
          <Input id="username" autoComplete="username" autoCapitalize="none" autoCorrect="off" spellCheck={false} required
                 value={username} onChange={(e) => setUsername(e.target.value)} aria-invalid={invalid} className="h-12 text-base" />
          {creating && <FieldDescription>Letters, numbers, dots, dashes or underscores.</FieldDescription>}
        </Field>
        <Field data-invalid={invalid}>
          <FieldLabel htmlFor="password">Password</FieldLabel>
          <Input id="password" type="password" autoComplete={creating ? "new-password" : "current-password"} required
                 minLength={creating ? 8 : undefined} value={password} onChange={(e) => setPassword(e.target.value)}
                 aria-invalid={invalid} className="h-12 text-base" />
          {creating && <FieldDescription>At least 8 characters.</FieldDescription>}
        </Field>
        {creating && (
          <Field data-invalid={mismatch || undefined}>
            <FieldLabel htmlFor="confirm">Confirm password</FieldLabel>
            <Input id="confirm" type="password" autoComplete="new-password" required value={confirm}
                   onChange={(e) => setConfirm(e.target.value)} aria-invalid={mismatch || undefined} className="h-12 text-base" />
            {mismatch && <FieldError>The passwords don&apos;t match.</FieldError>}
          </Field>
        )}
        {error && <FieldError>{error}</FieldError>}
      </FieldGroup>
      <Button type="submit" size="lg" disabled={busy || !username.trim() || !password || (creating && confirm !== password)} className="h-12 w-full text-base">
        <Spinner on={busy} />{creating ? "Save and sign in" : "Sign in"}
      </Button>
    </form>
  );
}

// The signed-out pages' frame (sign-in, setup, a setup link): the wordmark, one line, the form.
export function AuthShell({ title = "FFL", lede, children }: { title?: string; lede: string; children: React.ReactNode }) {
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-sm flex-col justify-center gap-8 px-4 pt-12 pb-[max(1.75rem,calc(env(safe-area-inset-bottom)+0.75rem))]">
      <div className="space-y-2">
        <h1 className="font-display text-[2.6rem] font-bold leading-none tracking-tight">{title}</h1>
        <p className="text-sm text-muted-foreground text-pretty">{lede}</p>
      </div>
      {children}
    </main>
  );
}
