"use client";

import { Ellipsis, Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "@/components/league-link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Item, ItemActions, ItemContent, ItemDescription, ItemMedia, ItemTitle } from "@/components/ui/item";
import { Switch } from "@/components/ui/switch";
import { byAccount, loginLabel, type EspnAccount, type EspnStatus, type ExtensionDevice, type LeagueChoice,
         type LeagueChoices, type PickResult } from "@/lib/espn";
import { PHONE_ACTION } from "./claims-submit";
import { espnGet, espnPost, TeamMark, why } from "./espn-api";
import { LeagueProblemMark } from "./league-problem";
import { Ago } from "./local-time";
import { Spinner } from "./motion";

// What a confirm dialog is about to do: stop following one league, disconnect one login, or every login.
type Confirm =
  | { kind: "leave"; league: LeagueChoice }
  | { kind: "login"; account: EspnAccount; leagues: LeagueChoice[] }
  | { kind: "all"; leagues: LeagueChoice[] };

// Settings › ESPN connection (#134, #460): each ESPN login with its state and every league ESPN lists on it, followed
// or not by a switch (on: added or joined; off: left, after a confirm); a login ESPN has since refused shows how to fix
// it and its leagues greyed with the problem. Add an ESPN login opens the wizard; the paired extensions unpair here;
// disconnecting a login (or every one) takes the user out of its leagues.
export function EspnConnection({ initial, devices: initialDevices }: { initial: EspnStatus; devices: ExtensionDevice[] }) {
  const router = useRouter();
  const [status, setStatus] = useState(initial);
  const [devices, setDevices] = useState(initialDevices);
  const [choices, setChoices] = useState<LeagueChoice[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);         // a league key, `login:<id>`, `device:<id>` or "all"
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);

  // state is set in the read's callbacks only, so the mount effect starts it without a cascading render
  const load = useCallback((fresh = false) =>
    Promise.all([
      espnGet<EspnStatus>("/espn/status"),
      espnGet<LeagueChoices>(`/espn/leagues${fresh ? "?fresh=true" : ""}`),
    ]).then(([st, list]) => {
      setListError(null);
      setStatus(st);
      setChoices(list.leagues);
    }, (e) => setListError(why(e, "Your leagues couldn't be read from ESPN just now."))), []);

  useEffect(() => { if (initial.accounts.length) void load(); }, [initial.accounts.length, load]);

  async function follow(l: LeagueChoice) {
    setBusy(l.key); setError(null);
    try {
      const r = await espnPost<{ leagues: PickResult[] }>("/espn/leagues", { ids: [l.league_id], default: null }, 90_000);
      if (!r.leagues[0]?.ok) setError(r.leagues[0]?.message ?? `${l.name} couldn't be added.`);
      await load();
      router.refresh();
    } catch (e) {
      setError(why(e));
    } finally {
      setBusy(null);
    }
  }

  async function act(c: Confirm) {
    const key = c.kind === "leave" ? c.league.key : c.kind === "login" ? `login:${c.account.id}` : "all";
    setBusy(key); setError(null);
    try {
      if (c.kind === "leave") await espnPost("/espn/leave", { league: c.league.key });
      else await espnPost("/espn/disconnect", c.kind === "login" ? { account: c.account.id } : {});
      setConfirm(null);
      // no league left: a full load of the start page sends them to /welcome (the layout reads the leagues once)
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      if (c.kind === "all") { window.location.assign("/"); return; }
      await load();
      router.refresh();
    } catch (e) {
      setError(why(e));
      setConfirm(null);
    } finally {
      setBusy(null);
    }
  }

  async function unpair(d: ExtensionDevice) {
    setBusy(`device:${d.id}`); setError(null);
    try {
      await espnPost(`/espn/devices/${d.id}/revoke`);
      setDevices((all) => all.filter((x) => x.id !== d.id));
    } catch (e) {
      setError(why(e));
    } finally {
      setBusy(null);
    }
  }

  const groups = byAccount(status.accounts, choices ?? []);
  const followed = (choices ?? []).filter((l) => l.joined);
  return (
    <Card id="espn" className="scroll-mt-4">
      <CardHeader>
        <CardTitle>ESPN connection</CardTitle>
        <CardDescription>FFL reads your leagues with these ESPN logins. Switch a league off to stop following it.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5 text-sm">
        {status.accounts.length === 0 && <p className="text-muted-foreground">No ESPN login is connected.</p>}
        {groups.map(({ account, leagues }) => (
          <section key={account.id} className="space-y-2" aria-label={loginLabel(status.accounts, account.id)}>
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <h3 className="font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em]">{loginLabel(status.accounts, account.id)}</h3>
                <p className={`text-xs ${account.connected ? "text-muted-foreground" : "text-destructive"}`}>
                  {account.unreadable ? "Can't be read here: connect it again"
                    : account.failing_since ? <>ESPN refused it <Ago iso={account.failing_since} /></>
                    : account.last_ok ? <>Connected · last read <Ago iso={account.last_ok} /></> : "Connected"}
                </p>
              </div>
              <DropdownMenu>
                <DropdownMenuTrigger render={<Button variant="ghost" size="icon" className="size-11" aria-label={`${loginLabel(status.accounts, account.id)} options`} />}>
                  <Ellipsis className="size-5" aria-hidden />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={() => load(true)}>Check its leagues again</DropdownMenuItem>
                  <DropdownMenuItem variant="destructive"
                                    onClick={() => setConfirm({ kind: "login", account, leagues: leagues.filter((l) => l.joined) })}>
                    Disconnect this login…
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
            {!account.connected && !account.unreadable && (
              <p className="leading-relaxed">
                Sign in to ESPN with this login again in the browser where the FFL extension is on. It sends the new
                login and this clears by itself.
              </p>
            )}
            {choices === null && !listError
              ? <p className="flex items-center gap-2 text-muted-foreground"><Spinner on gap="" />Reading its leagues from ESPN…</p>
              : leagues.length > 0 && (
                <div className="divide-y divide-foreground/8 rounded-lg ring-1 ring-foreground/10">
                  {leagues.map((l) => (
                    <FollowRow key={l.key} league={l} busy={busy === l.key} disabled={busy !== null}
                               onFollow={() => follow(l)} onLeave={() => setConfirm({ kind: "leave", league: l })} />
                  ))}
                </div>
              )}
          </section>
        ))}
        {listError && <p role="status" className="text-destructive">{listError}</p>}
        {error && <p role="status" className="log-in text-destructive">{error}</p>}

        <Button variant="outline" className={`w-full ${PHONE_ACTION}`} render={<a href="/welcome?add=1" />} nativeButton={false}>
          <Plus aria-hidden />Add an ESPN login
        </Button>

        {devices.length > 0 && (
          <section className="space-y-2 border-t border-foreground/10 pt-4">
            <h3 className="font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">Paired extensions</h3>
            <ul className="divide-y divide-foreground/10">
              {devices.map((d) => (
                <li key={d.id} className="flex min-h-12 items-center justify-between gap-3 py-2 first:pt-0 last:pb-0">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{d.label}</p>
                    <p className="text-xs text-muted-foreground">
                      {d.last_used_at ? <>Last sent <Ago iso={d.last_used_at} /></> : <>Paired <Ago iso={d.created_at} /></>}
                    </p>
                  </div>
                  <Button variant="ghost" size="sm" onClick={() => unpair(d)} disabled={busy !== null}
                          className="shrink-0 max-lg:h-10 max-lg:px-3" aria-label={`Unpair ${d.label}`}>
                    <Spinner on={busy === `device:${d.id}`} />Unpair
                  </Button>
                </li>
              ))}
            </ul>
          </section>
        )}

        <div className="flex flex-col items-start gap-3 border-t border-foreground/10 pt-4">
          <a href="/welcome?add=1&paste=1" className="text-muted-foreground underline underline-offset-4 hover:text-foreground">
            Paste a login by hand instead
          </a>
          {status.accounts.length > 0 && (
            <Button variant="destructive" onClick={() => setConfirm({ kind: "all", leagues: followed })} disabled={busy !== null}
                    className={PHONE_ACTION}>
              Disconnect every ESPN login
            </Button>
          )}
        </div>
      </CardContent>

      <Dialog open={confirm !== null} onOpenChange={(o) => { if (!o && !busy) setConfirm(null); }}>
        <DialogContent>
          {confirm && <ConfirmBody c={confirm} label={confirm.kind === "login" ? loginLabel(status.accounts, confirm.account.id) : ""} />}
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setConfirm(null)} disabled={busy !== null} className={PHONE_ACTION}>Cancel</Button>
            {confirm && (
              <Button variant={confirm.kind === "leave" ? "default" : "destructive"} onClick={() => act(confirm)}
                      disabled={busy !== null} className={PHONE_ACTION}>
                <Spinner on={busy !== null} />{confirm.kind === "leave" ? "Stop following" : "Disconnect"}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function ConfirmBody({ c, label }: { c: Confirm; label: string }) {
  const kept = "Your decisions and watchlist there are kept if you add it again.";
  if (c.kind === "leave") {
    return (
      <DialogHeader>
        <DialogTitle>Stop following {c.league.name}?</DialogTitle>
        <DialogDescription>It leaves the league switcher and its notifications stop. {kept}</DialogDescription>
      </DialogHeader>
    );
  }
  return (
    <>
      <DialogHeader>
        <DialogTitle>{c.kind === "login" ? `Disconnect ${label}?` : "Disconnect every ESPN login?"}</DialogTitle>
        <DialogDescription>
          {c.leagues.length
            ? `FFL deletes ${c.kind === "login" ? "this login" : "them"} and you stop following ${c.leagues.length === 1 ? "its league" : "these leagues"}:`
            : `FFL deletes ${c.kind === "login" ? "this login" : "them"}. You follow no league through ${c.kind === "login" ? "it" : "them"}.`}
        </DialogDescription>
      </DialogHeader>
      {c.leagues.length > 0 && (
        <ul className="list-disc space-y-1 pl-5 text-sm">{c.leagues.map((l) => <li key={l.key}>{l.name}</li>)}</ul>
      )}
      <p className="text-sm leading-relaxed text-muted-foreground">
        {kept} {c.kind === "login" ? "Your other logins and the paired extensions stay." : "The paired extensions are unpaired too."}
      </p>
    </>
  );
}

function FollowRow({ league: l, busy, disabled, onFollow, onLeave }: {
  league: LeagueChoice; busy: boolean; disabled: boolean; onFollow: () => void; onLeave: () => void;
}) {
  const dim = !l.ok;
  const sub = [l.team, !l.joined && "not followed", l.joined && dim && "your moves wait for this login"].filter(Boolean).join(" · ");
  return (
    <Item size="sm" className="rounded-none px-3.5 py-3">
      <ItemMedia><TeamMark logo={l.logo} team={l.team ?? l.name} dim={dim} /></ItemMedia>
      <ItemContent className={`min-w-0 gap-0.5 ${dim ? "opacity-60" : ""}`}>
        <ItemTitle className="font-display text-lg font-semibold leading-tight">{l.name}</ItemTitle>
        <ItemDescription className="text-xs">{sub}</ItemDescription>
      </ItemContent>
      <ItemActions>
        {l.problem && <LeagueProblemMark problem={l.problem} league={l.name} />}
        {busy && <Spinner on gap="" />}
        {/* a league ESPN won't let this login pick can only be switched off */}
        {(l.joined || l.ok) && (
          <Switch checked={l.joined} disabled={disabled} aria-label={`Follow ${l.name}`}
                  onCheckedChange={(on) => (on ? onFollow() : onLeave())} />
        )}
      </ItemActions>
    </Item>
  );
}
