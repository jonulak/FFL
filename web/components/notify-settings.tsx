"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { useRouter } from "next/navigation";
import { clientPost } from "@/app/api-client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import type { Notice, NotifyConfig, NotifyPrefs } from "@/lib/api";
import { isHm, noticeState, pushSupport, readPushEnv, urlBase64ToUint8Array } from "@/lib/notify";

const KINDS = [["move", "League moves", "Adds, drops and trades, batched per sync"],
               ["injury", "My starter hurt", "OUT, Doubtful or IR before his game locks"],
               ["waivers", "Waiver results", "Claims won and lost after the run"]] as const;

const noSubscribe = () => () => {};

export function NotifySettings({ config, recent }: { config: NotifyConfig; recent: Notice[] }) {
  const router = useRouter();
  const [prefs, setPrefs] = useState<NotifyPrefs>(config.prefs);
  // the browser's push capability: null on the server and the first client render, then fixed
  const support = useSyncExternalStore(noSubscribe, () => pushSupport(readPushEnv()), () => null);
  const [subscribed, setSubscribed] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (support === "ready") navigator.serviceWorker.ready.then((r) => r.pushManager.getSubscription()).then((sub) => setSubscribed(!!sub));
  }, [support]);

  async function run(fn: () => Promise<string | void>) {
    setBusy(true); setMsg(null);
    try { const m = await fn(); if (m) setMsg(m); router.refresh(); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  const enable = () => run(async () => {
    if (!config.public_key) return "The API has no VAPID key yet.";
    if ((await Notification.requestPermission()) !== "granted") return "Notifications were not allowed.";
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(config.public_key) });
    const j = sub.toJSON();
    await clientPost("/notify/subscribe", { endpoint: j.endpoint, keys: j.keys, ua: navigator.userAgent });
    setSubscribed(true);
  });

  const disable = () => run(async () => {
    const sub = await (await navigator.serviceWorker.ready).pushManager.getSubscription();
    if (sub) { await clientPost("/notify/unsubscribe", { endpoint: sub.endpoint }); await sub.unsubscribe(); }
    setSubscribed(false);
  });

  const save = (p: Partial<NotifyPrefs>) => run(async () => {
    setPrefs(await clientPost<NotifyPrefs>("/notify/prefs", p));
  });

  const test = () => run(async () => {
    const r = await clientPost<{ devices: number; accepted: boolean }>("/notify/test");
    return r.accepted ? `Sent to ${r.devices} device${r.devices === 1 ? "" : "s"}.` : "No device took it.";
  });

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader><CardTitle>This device</CardTitle></CardHeader>
        <CardContent className="space-y-3 text-sm">
          {support === "needs-install" && <p>On iPhone, notifications work only from the Home Screen app: Share → Add to Home Screen, then open FFL from there.</p>}
          {support === "unsupported" && <p>This browser cannot receive push notifications.</p>}
          {support === "ready" && (
            <div className="flex flex-wrap items-center gap-2">
              {subscribed
                ? <Button variant="outline" onClick={disable} disabled={busy}>Turn off on this device</Button>
                : <Button onClick={enable} disabled={busy}>Enable on this device</Button>}
              <Button variant="outline" onClick={test} disabled={busy || !config.enabled}>Send test</Button>
            </div>
          )}
          <p className="text-muted-foreground">
            {config.devices} device{config.devices === 1 ? "" : "s"} subscribed{config.enabled ? "" : " · sending is off on this API"}
          </p>
          {msg && <p role="status">{msg}</p>}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Notify me about</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          {KINDS.map(([k, label, hint]) => (
            <Label key={k} className="flex items-center justify-between gap-4">
              <span><span className="font-medium">{label}</span><span className="block text-xs text-muted-foreground">{hint}</span></span>
              <Switch checked={prefs[k]} onCheckedChange={(v) => save({ [k]: v })} disabled={busy} />
            </Label>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Quiet hours</CardTitle></CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2 text-sm">
          <Input type="time" className="w-32" defaultValue={prefs.quiet[0]} aria-label="Quiet from"
                 onBlur={(e) => isHm(e.target.value) && e.target.value !== prefs.quiet[0] && save({ quiet: [e.target.value, prefs.quiet[1]] })} />
          <span>to</span>
          <Input type="time" className="w-32" defaultValue={prefs.quiet[1]} aria-label="Quiet until"
                 onBlur={(e) => isHm(e.target.value) && e.target.value !== prefs.quiet[1] && save({ quiet: [prefs.quiet[0], e.target.value] })} />
          <span className="text-muted-foreground">held, then sent together</span>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Recent notices</CardTitle></CardHeader>
        <CardContent>
          {recent.length === 0 ? <p className="text-sm text-muted-foreground">None yet.</p> : (
            <ul className="divide-y divide-foreground/8 text-sm">
              {recent.map((n) => (
                <li key={n.key} className="flex items-baseline gap-3 py-1.5">
                  <span className="min-w-0 flex-1 truncate"><span className="font-medium">{n.title}</span> · {n.body}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">{noticeState(n)} · {n.created_at.slice(5, 16).replace("T", " ")}</span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
