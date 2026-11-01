"use client";

import { useState } from "react";
import { ClientError, clientPost } from "@/app/api-client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import type { AppSettings } from "@/lib/api";
import { PHONE_ACTION } from "./claims-submit";
import { CredentialSteps, Out, Ui } from "./credential-steps";
import { Spinner } from "./motion";

type Msg = { ok: boolean; text: string } | null;
type Body = Partial<{ app_url: string; push_contact: string; youtube_key: string }>;

const INPUT = "max-lg:h-12 max-lg:text-base";

// Why POST /app/settings refused: its own message (422 bad_url / bad_contact), else the status line.
function saveError(err: unknown): string {
  if (err instanceof ClientError) {
    const m = (err.body as { message?: unknown } | null)?.message;
    if (typeof m === "string" && m) return m;
  }
  return `Not saved: ${err instanceof Error ? err.message : String(err)}`;
}

function useSave(onSaved: (s: AppSettings) => void) {
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  async function save(what: string, body: Body, ok: string) {
    setBusy(what); setMsg(null);
    try {
      onSaved(await clientPost<AppSettings>("/app/settings", body));
      setMsg({ ok: true, text: ok });
    } catch (err) {
      setMsg({ ok: false, text: saveError(err) });
    }
    setBusy(null);
  }
  return { busy, msg, save };
}

function Status({ msg }: { msg: Msg }) {
  return msg && (msg.ok ? <p role="status">{msg.text}</p> : <FieldError>{msg.text}</FieldError>);
}

// The install's own settings (admins: GET/POST /app/settings), kept in the database across upgrades. Settings › App
// shows every card; the first-run wizard's Services step shows the optional ones (`optionalOnly`).
export function AppSettingsCards({ initial, optionalOnly = false, onSaved }: {
  initial: AppSettings; optionalOnly?: boolean; onSaved?: () => void;
}) {
  const [settings, setSettings] = useState(initial);
  const saved = (s: AppSettings) => { setSettings(s); onSaved?.(); };
  return (
    <>
      {!optionalOnly && <AppUrlCard settings={settings} onSaved={setSettings} />}
      <PushContactCard settings={settings} onSaved={saved} />
      <YoutubeCard settings={settings} onSaved={saved} />
    </>
  );
}

function AppUrlCard({ settings, onSaved }: { settings: AppSettings; onSaved: (s: AppSettings) => void }) {
  const [url, setUrl] = useState(settings.app_url ?? "");
  const { busy, msg, save } = useSave(onSaved);
  const invalid = msg && !msg.ok ? true : undefined;
  return (
    <Card>
      <CardHeader><CardTitle>App address</CardTitle></CardHeader>
      <CardContent className="text-sm">
        <form onSubmit={(e) => { e.preventDefault(); save("url", { app_url: url }, "Saved."); }} className="space-y-3">
          <Field data-invalid={invalid}>
            <FieldLabel htmlFor="app-url">Address people open FFL at</FieldLabel>
            <Input id="app-url" type="url" inputMode="url" autoComplete="off" spellCheck={false} required value={url}
                   placeholder="https://ffl.example.com" onChange={(e) => setUrl(e.target.value)} aria-invalid={invalid}
                   className={`font-mono ${INPUT}`} />
            <FieldDescription>
              Notification taps and invite links open here. Set when you first signed up; change it if you move FFL
              behind a new address. Notifications on iPhone need an https address.
            </FieldDescription>
          </Field>
          <Status msg={msg} />
          <Button type="submit" disabled={busy !== null || !url.trim() || url.trim() === settings.app_url}
                  className={PHONE_ACTION}>
            <Spinner on={busy === "url"} />Save
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function PushContactCard({ settings, onSaved }: { settings: AppSettings; onSaved: (s: AppSettings) => void }) {
  const [email, setEmail] = useState(settings.push_contact ?? "");
  const { busy, msg, save } = useSave(onSaved);
  const invalid = msg && !msg.ok ? true : undefined;
  return (
    <Card>
      <CardHeader><CardTitle>Notification contact</CardTitle></CardHeader>
      <CardContent className="text-sm">
        <form onSubmit={(e) => { e.preventDefault(); save("contact", { push_contact: email }, email.trim() ? "Saved." : "Cleared."); }}
              className="space-y-3">
          <Field data-invalid={invalid}>
            <FieldLabel htmlFor="push-contact">Your email <span className="font-normal text-muted-foreground">(optional)</span></FieldLabel>
            <Input id="push-contact" type="email" inputMode="email" autoComplete="email" spellCheck={false} value={email}
                   placeholder="you@example.com" onChange={(e) => setEmail(e.target.value)} aria-invalid={invalid}
                   className={INPUT} />
            <FieldDescription>
              Sent with every notification so Apple and Google can reach you if this server&apos;s notifications
              misbehave. Nothing else uses it. Left blank, the app address goes instead.
            </FieldDescription>
          </Field>
          <Status msg={msg} />
          <Button type="submit" disabled={busy !== null || email.trim() === (settings.push_contact ?? "")}
                  className={PHONE_ACTION}>
            <Spinner on={busy === "contact"} />Save
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

const YT_CONSOLE = "https://console.cloud.google.com/";
const YT_LIBRARY = "https://console.cloud.google.com/apis/library/youtube.googleapis.com";
const YT_CREDENTIALS = "https://console.cloud.google.com/apis/credentials";

function YoutubeKeySteps() {
  return (
    <CredentialSteps label="How to get a key" title="Get a YouTube API key"
                     description="Free, with any Google account. About five minutes."
                     footnote="Each check of a channel spends 1 of the 10,000 free units Google gives a key a day.">
          <li>Open the <Out href={YT_CONSOLE}>Google Cloud console</Out> and sign in.</li>
          <li>
            Make a project: open the project picker at the top, choose <Ui>New project</Ui>, name it (FFL is fine) and
            choose <Ui>Create</Ui>. No billing account is needed.
          </li>
          <li>
            With that project selected, open <Out href={YT_LIBRARY}>YouTube Data API v3</Out> and choose <Ui>Enable</Ui>.
          </li>
          <li>
            Open <Out href={YT_CREDENTIALS}>Credentials</Out>, choose <Ui>Create credentials › API key</Ui>, and copy
            the key it shows.
          </li>
          <li>
            Recommended: choose <Ui>Edit API key</Ui>, under <Ui>API restrictions</Ui> pick <Ui>Restrict key</Ui> and
            tick <Ui>YouTube Data API v3</Ui>, then <Ui>Save</Ui>. A leaked key then can&apos;t be used for anything else.
          </li>
          <li>Back here, paste the key into <Ui>YouTube API key</Ui> and choose <Ui>Save</Ui>.</li>
    </CredentialSteps>
  );
}

function YoutubeCard({ settings, onSaved }: { settings: AppSettings; onSaved: (s: AppSettings) => void }) {
  const [key, setKey] = useState("");
  const { busy, msg, save } = useSave((s) => { setKey(""); onSaved(s); });
  const invalid = msg && !msg.ok ? true : undefined;
  const set = settings.youtube_key_set;
  return (
    <Card>
      <CardHeader><CardTitle>YouTube</CardTitle></CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-muted-foreground">
          {set ? "Connected: the news desk finds new press conferences through the YouTube API."
            : "Optional. Without a key the news desk reads each club's public video feed, which is slower to show new press conferences and sometimes down for hours."}
        </p>
        <YoutubeKeySteps />
        <form onSubmit={(e) => { e.preventDefault(); save("key", { youtube_key: key }, "Saved."); }} className="space-y-3">
          <Field data-invalid={invalid}>
            <FieldLabel htmlFor="youtube-key">{set ? "Replace the key" : "YouTube API key"}</FieldLabel>
            <Input id="youtube-key" type="password" autoComplete="off" spellCheck={false} required value={key}
                   onChange={(e) => setKey(e.target.value)} aria-invalid={invalid} className={`font-mono ${INPUT}`} />
          </Field>
          <Status msg={msg} />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy !== null || !key.trim()} className={PHONE_ACTION}>
              <Spinner on={busy === "key"} />Save
            </Button>
            {set && (
              <Button type="button" variant="outline" disabled={busy !== null} className={PHONE_ACTION}
                      onClick={() => save("remove", { youtube_key: "" }, "Removed.")}>
                <Spinner on={busy === "remove"} />Remove
              </Button>
            )}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
