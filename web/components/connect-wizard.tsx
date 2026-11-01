"use client";

import { Check, Copy, ExternalLink, LoaderCircle, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Item, ItemActions, ItemContent, ItemDescription, ItemMedia, ItemTitle } from "@/components/ui/item";
import { Label } from "@/components/ui/label";
import type { LlmToken } from "@/lib/api";
import { wizardStart } from "@/lib/llm";
import { arrivedAccount, canPaste, newRefusal, pickable, resentAccount, type EspnStatus, type LeagueChoice, type LeagueChoices, type PickResult,
         type Platform } from "@/lib/espn";
import { PHONE_ACTION } from "./claims-submit";
import { espnGet, espnPost, TeamMark, why } from "./espn-api";
import { LeagueProblemMark } from "./league-problem";
import { LlmSettings } from "./llm-settings";
import { Spinner } from "./motion";

// Where each platform installs the extension, when the store listing exists (the box's env; none: no button).
export type InstallLinks = { testflight: string | null; chrome: string | null; mac: string | null };

type Step = "claude" | "connect" | "leagues" | "setup";
const STEPS: [Step, string][] = [["connect", "Connect"], ["leagues", "Leagues"], ["setup", "Set up"]];
const POLL_MS = 2000;

// The connect wizard (ffl-soc.6.3.5, #133): connect one ESPN login (the extension's pairing code, or a paste on a
// computer), pick the leagues that login is in, set them up one at a time, then open one. A first run (no league yet)
// skips Connect when a working login is already stored; Add an ESPN login (`add`, from Settings) waits for a login
// that wasn't there when it opened. One login at a time: Settings manages the leagues of the others.
// An admin on a server with no Claude token (`claude`, fetched for admins only) is asked for one first, and can skip it.
export function ConnectWizard({ platform, links, initial, add, hasLeagues, startPaste, claude }: {
  platform: Platform; links: InstallLinks; initial: EspnStatus; add: boolean; hasLeagues: boolean; startPaste: boolean;
  claude: LlmToken | null;
}) {
  const firstRun = !add;
  const ready = firstRun ? arrivedAccount(initial, [], true) : null;
  const [step, setStep] = useState<Step>(wizardStart(claude, ready !== null));
  const steps: [Step, string][] = claude && !claude.set ? [["claude", "Claude"], ...STEPS] : STEPS;
  const [account, setAccount] = useState<number | null>(ready?.id ?? null);
  const [picked, setPicked] = useState<{ leagues: LeagueChoice[]; def: string | null } | null>(null);

  return (
    <div className="flex flex-col gap-6">
      <nav aria-label="Setup steps" className="flex border-b border-foreground/12">
        {steps.map(([s, label], i) => (
          <span key={s} aria-current={s === step ? "step" : undefined}
                className={`font-display px-3 max-sm:px-2.5 pb-2.5 text-[0.8125rem] font-semibold uppercase tracking-[0.12em] first:pl-0 ${
                  s === step ? "text-foreground shadow-[inset_0_-2px_0_var(--foreground)]" : "text-foreground/45"}`}>
            {i + 1} {label}
          </span>
        ))}
      </nav>
      {step === "claude" && claude && <ClaudeStep initial={claude} onDone={() => setStep(ready ? "leagues" : "connect")} />}
      {step === "connect" && (
        <ConnectStep platform={platform} links={links} initial={initial} firstRun={firstRun} startPaste={startPaste}
                     onArrived={(id) => { setAccount(id); setStep("leagues"); }} />
      )}
      {step === "leagues" && account !== null && (
        <LeaguesStep account={account} hasLeagues={hasLeagues}
                     onBack={() => setStep("connect")}
                     onPicked={(leagues, def) => { setPicked({ leagues, def }); setStep("setup"); }} />
      )}
      {step === "setup" && picked && (
        <SetupStep leagues={picked.leagues} def={picked.def} onBack={() => setStep("leagues")} />
      )}
    </div>
  );
}

function Heading({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <h1 className="font-display text-4xl font-bold leading-none tracking-tight text-balance">{title}</h1>
      <p className="max-w-[60ch] text-sm leading-relaxed text-muted-foreground">{children}</p>
    </div>
  );
}

function StepList({ steps }: { steps: React.ReactNode[] }) {
  return (
    <ol className="space-y-3.5">
      {steps.map((s, i) => (
        <li key={i} className="grid grid-cols-[1.625rem_1fr] items-start gap-3">
          <span className="font-display flex h-6 items-center justify-center rounded-sm bg-foreground/10 text-[0.9375rem] font-semibold">{i + 1}</span>
          <div className="text-[15px] font-medium leading-snug">{s}</div>
        </li>
      ))}
    </ol>
  );
}

function InstallButton({ href, children }: { href: string | null; children: string }) {
  if (!href) return null;
  return (
    <Button variant="outline" className="mt-2 h-9" render={<a href={href} target="_blank" rel="noreferrer" />} nativeButton={false}>
      {children}
    </Button>
  );
}

// Installing the extension, per browser, then connecting from its icon on this page: the extension reads which FFL
// this tab is and who is signed in, and asks the browser to allow this site when Connect is clicked.
function stepsFor(platform: Platform, links: InstallLinks, homeScreen: boolean): React.ReactNode[] {
  if (platform === "ios") {
    return [
      <>Install the FFL app<InstallButton href={links.testflight}>Open TestFlight</InstallButton></>,
      <>Open it and turn the extension on <span className="font-normal text-muted-foreground">(it walks you through Settings)</span></>,
      ...(homeScreen ? ["Open this page in Safari"] : []),
      <>On this page, tap the page menu left of the address bar, then <b>FFL</b>, then <b>Connect</b></>,
    ];
  }
  if (platform === "mac-safari") {
    return [
      <>Install the FFL app for Mac<InstallButton href={links.mac}>Get the Mac app</InstallButton></>,
      "Open it and turn the extension on in Safari Settings › Extensions",
      <>On this page, click the FFL icon in Safari&apos;s toolbar, then <b>Connect</b></>,
    ];
  }
  return [
    <>Add FFL to Chrome<InstallButton href={links.chrome}>Chrome Web Store</InstallButton></>,
    <>On this page, click the FFL icon in Chrome&apos;s toolbar <span className="font-normal text-muted-foreground">(under the puzzle piece until it&apos;s pinned)</span>, then <b>Connect</b></>,
  ];
}

const noSubscribe = () => () => {};

// FFL opened from the Home Screen: Safari's extensions don't run here, and its cookies are its own.
function homeScreenApp(): boolean {
  return window.matchMedia?.("(display-mode: standalone)").matches
    || (navigator as Navigator & { standalone?: boolean }).standalone === true;
}

// --- 0 Claude (an admin, on a server with no token) ---

function ClaudeStep({ initial, onDone }: { initial: LlmToken; onDone: () => void }) {
  return (
    <section className="space-y-5">
      <Heading title="Connect Claude">
        FFL writes the Roster outlooks and the news write-ups with Claude. Without a token they don&apos;t run; everything
        else does. You can add it later in Settings.
      </Heading>
      <LlmSettings initial={initial} onSaved={() => window.setTimeout(onDone, 700)} />
      <Button variant="ghost" onClick={onDone} className={PHONE_ACTION}>Skip for now</Button>
    </section>
  );
}

const ESPN_FANTASY = "https://fantasy.espn.com/football/";

// --- 1 Connect ---

function ConnectStep({ platform, links, initial, firstRun, startPaste, onArrived }: {
  platform: Platform; links: InstallLinks; initial: EspnStatus; firstRun: boolean; startPaste: boolean;
  onArrived: (account: number) => void;
}) {
  const before = useRef(initial.accounts.map((a) => a.id));
  const stamps = useRef(initial.accounts.map(({ id, updated_at }) => ({ id, updated_at })));
  const [resent, setResent] = useState(false);   // adding: the login that arrived was one already connected
  // false on the server, the real answer after hydration (no window there)
  const homeScreen = useSyncExternalStore(noSubscribe, homeScreenApp, () => false);
  const [linked, setLinked] = useState(false);   // this browser linked by a typed code
  const [useCode, setUseCode] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [arrived, setArrived] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);   // the extension sent a login ESPN turned down
  const [paste, setPaste] = useState(startPaste && canPaste(platform));
  const done = useRef(false);

  const land = useCallback((id: number) => {
    if (done.current) return;
    done.current = true;
    setArrived(true);
    window.setTimeout(() => onArrived(id), 700);   // the tick shows before the page moves on
  }, [onArrived]);

  // A linked extension sends the login on its own (just now, or linked before signing in to another login): watch the
  // connection while the page is in view.
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      if (document.visibilityState !== "visible" || done.current) return;
      try {
        const st = await espnGet<EspnStatus>("/espn/status");
        const a = alive ? arrivedAccount(st, before.current, firstRun) : null;
        if (a) land(a.id);
        else if (alive && !firstRun && resentAccount(st, stamps.current)) setResent(true);
        if (alive) setRefused(newRefusal(st, initial)?.message ?? null);
      } catch { /* the next tick tries again */ }
    };
    const t = window.setInterval(tick, POLL_MS);
    return () => { alive = false; window.clearInterval(t); };
  }, [firstRun, land, initial]);

  const pair = async (c: string) => { await espnPost<{ ok: true; label: string }>("/espn/pair/confirm", { code: c }); };

  async function confirm(e: React.FormEvent) {
    e.preventDefault();
    if (busy || !code.trim()) return;
    setBusy(true); setError(null);
    try {
      await pair(code);
      setLinked(true);
    } catch (err) {
      setError(why(err));
    } finally {
      setBusy(false);
    }
  }

  const other = platform === "other";
  const codeForm = (
    <form onSubmit={confirm} className="space-y-3">
      <Label htmlFor="pair-code" className="font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        The code the extension shows
      </Label>
      <Input id="pair-code" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} disabled={busy}
             placeholder="XXXX-XXXX" autoComplete="one-time-code" autoCapitalize="characters" spellCheck={false}
             maxLength={12} aria-invalid={error ? true : undefined}
             className="font-display h-14 text-center text-3xl font-semibold tracking-[0.18em] uppercase md:text-3xl" />
      <Button type="submit" disabled={busy || !code.trim()} className={`w-full ${PHONE_ACTION}`}>
        <Spinner on={busy} />Connect
      </Button>
      <p role="status" className={`text-center text-xs leading-4 ${error ? "text-destructive" : "text-muted-foreground"}`}>
        {error ?? <>In the extension: <b className="font-medium">Pair with a code instead</b>, then copy its code here. Codes last 10 minutes.</>}
      </p>
    </form>
  );
  const codeLink = (label: string) => (
    <button type="button" onClick={() => { setUseCode(true); setError(null); }}
            className="text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground">
      {label}
    </button>
  );

  let card: React.ReactNode;
  if (linked) {
    card = (
      <ul className="space-y-3 text-sm" aria-live="polite">
        <Line done>This browser is connected</Line>
        <Line done={arrived}>{arrived ? "ESPN login received" : "Waiting for your ESPN login…"}</Line>
        {!arrived && (
          <li className="space-y-3 pt-1">
            <p className="leading-relaxed text-muted-foreground">
              Open ESPN Fantasy and sign in if it asks. The extension sends your login here by itself; then come back to
              this page.
            </p>
            <Button className={`w-full ${PHONE_ACTION}`} render={<a href={ESPN_FANTASY} target="_blank" rel="noreferrer" />}
                    nativeButton={false}>
              Open ESPN Fantasy<ExternalLink aria-hidden />
            </Button>
          </li>
        )}
      </ul>
    );
  } else if (useCode) {
    card = codeForm;
  } else if (homeScreen) {
    card = (
      <div className="space-y-3 text-sm">
        <p className="font-medium">Connect in Safari</p>
        <p className="leading-relaxed text-muted-foreground">
          Safari&apos;s extensions don&apos;t run in the Home Screen app. Open FFL in Safari, sign in, and connect there:
          your leagues then show up here too.
        </p>
        <CopyLink />
        {codeLink("Use a code instead")}
      </div>
    );
  } else {
    // the extension pairs from its own popup on this page; the poll above moves on when the login arrives
    card = (
      <div className="space-y-3 text-sm">
        <ul aria-live="polite"><Line done={false}>Waiting for the extension…</Line></ul>
        <p className="leading-relaxed text-muted-foreground">
          Click the FFL icon in your browser&apos;s toolbar while this page is open, then <b className="font-medium text-foreground">Connect</b>.
          Then sign in to ESPN in this browser: this page moves on when your login arrives.
        </p>
        <Button variant="outline" className={`w-full ${PHONE_ACTION}`} render={<a href={ESPN_FANTASY} target="_blank" rel="noreferrer" />}
                nativeButton={false}>
          Open ESPN Fantasy<ExternalLink aria-hidden />
        </Button>
        {codeLink("Use a code instead")}
      </div>
    );
  }

  // a phone reads it top to bottom (intro, the card, the rest); a computer sets the card beside the steps (the
  // mockup's Desktop board: steps left, a 360px column right)
  return (
    <div className="grid gap-6 md:grid-cols-[minmax(0,1fr)_22.5rem] md:grid-rows-[auto_1fr] md:items-start md:gap-x-12">
      <div className="flex flex-col gap-6">
        <Heading title={firstRun ? "Connect ESPN" : "Add an ESPN login"}>
          {other
            ? "FFL reads your leagues with your ESPN login. The FFL extension sends it here: it runs in Safari on iPhone, iPad and Mac, and in Chrome on a computer. Open this page there."
            : firstRun
              ? "FFL reads your leagues with your ESPN login. Connect the FFL extension from its icon on this page, then sign in to ESPN: the extension sends your login here."
              : initial.devices > 0
                ? "Sign in to ESPN with the other login where your FFL extension is on: it sends the new login here by itself."
                : "Connect the FFL extension from its icon on this page, then sign in to ESPN with the other login."}
        </Heading>

        {!linked && !other && <StepList steps={stepsFor(platform, links, homeScreen)} />}
      </div>

      {!other && (
        <Card className="md:col-start-2 md:row-span-2 md:row-start-1">
          <CardContent className="space-y-3">{card}</CardContent>
        </Card>
      )}

      <div className="flex flex-col gap-6 empty:hidden md:col-start-1">
        {/* an extension linked here or before sent a login ESPN turned down: say so rather than wait on */}
        {refused && !arrived && (
          <p role="alert" className="flex items-start gap-2.5 text-sm leading-relaxed text-destructive">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
            <span>{refused} The extension sends the new login by itself.</span>
          </p>
        )}
        {resent && (
          <p role="status" className="text-sm leading-relaxed">
            That ESPN login is already connected: its leagues are in{" "}
            <a href="/settings" className="underline underline-offset-4">Settings</a>. To add another, sign out of ESPN
            and sign in with the other login.
          </p>
        )}

        {canPaste(platform) && (paste
          ? <PasteForm onConnected={land} onCancel={other ? undefined : () => setPaste(false)} />
          : <button type="button" onClick={() => setPaste(true)}
                    className="self-start text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground">
              No extension? Paste them instead
            </button>)}
      </div>
    </div>
  );
}

// The page's own address to open in Safari from the Home Screen app, where a link to this site would stay in the app.
function CopyLink() {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
    } catch { /* no clipboard: the address is the site's own, typed by hand */ }
  }
  return (
    <Button variant="outline" onClick={copy} className={`w-full ${PHONE_ACTION}`}>
      {copied ? <Check aria-hidden /> : <Copy aria-hidden />}{copied ? "Copied: paste it in Safari" : "Copy this page's link"}
    </Button>
  );
}

function Line({ done, children }: { done: boolean; children: React.ReactNode }) {
  return (
    <li className={`flex items-center gap-2.5 ${done ? "" : "text-muted-foreground"}`}>
      {done ? <Check className="size-4.5 shrink-0" aria-hidden /> : <LoaderCircle className="size-4.5 shrink-0 animate-spin" aria-hidden />}
      <span>{children}</span>
    </li>
  );
}

function PasteForm({ onConnected, onCancel }: { onConnected: (account: number) => void; onCancel?: () => void }) {
  const [s2, setS2] = useState("");
  const [swid, setSwid] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy || !s2.trim() || !swid.trim()) return;
    setBusy(true); setError(null);
    try {
      const r = await espnPost<{ account: number }>("/espn/connect", { espn_s2: s2, swid });
      setS2(""); setSwid("");
      onConnected(r.account);
    } catch (err) {
      setError(why(err));
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <h2 className="font-display text-xl font-semibold leading-tight">Paste them instead</h2>
          <ol className="list-decimal space-y-1 pl-5 text-sm leading-relaxed">
            <li>Sign in to ESPN Fantasy, then open the browser&apos;s developer tools.</li>
            <li>Chrome: <b>Application</b> › Cookies › espn.com. Safari: <b>Storage</b> › Cookies.</li>
            <li>Copy the values of <code className="font-mono text-[0.8125rem]">espn_s2</code> and <code className="font-mono text-[0.8125rem]">SWID</code>, whole.</li>
          </ol>
          <p className="text-xs leading-4 text-muted-foreground">You&apos;ll paste again each time ESPN signs you out; the extension does that for you.</p>
        </div>
        <form onSubmit={submit} className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="paste-s2">espn_s2</Label>
            <Input id="paste-s2" type="password" autoComplete="off" spellCheck={false} value={s2}
                   onChange={(e) => setS2(e.target.value)} disabled={busy} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="paste-swid">SWID</Label>
            <Input id="paste-swid" type="password" autoComplete="off" spellCheck={false} value={swid}
                   onChange={(e) => setSwid(e.target.value)} disabled={busy} />
          </div>
          {error && <p role="status" className="text-sm text-destructive">{error}</p>}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy || !s2.trim() || !swid.trim()} className={PHONE_ACTION}>
              <Spinner on={busy} />Connect
            </Button>
            {onCancel && <Button type="button" variant="ghost" onClick={onCancel} disabled={busy} className={PHONE_ACTION}>Use the extension</Button>}
          </div>
          <p className="text-xs leading-4 text-muted-foreground">Checked with ESPN, then kept encrypted. Never shown again.</p>
        </form>
      </CardContent>
    </Card>
  );
}

// --- 2 Leagues ---

function LeaguesStep({ account, hasLeagues, onBack, onPicked }: {
  account: number; hasLeagues: boolean; onBack: () => void; onPicked: (leagues: LeagueChoice[], def: string | null) => void;
}) {
  const [data, setData] = useState<LeagueChoices | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [checked, setChecked] = useState<string[]>([]);
  const [def, setDef] = useState<string | null>(null);

  // state is set in the read's callbacks only, so the mount effect starts it without a cascading render
  const read = useCallback((fresh: boolean) =>
    espnGet<LeagueChoices>(`/espn/leagues?account=${account}${fresh ? "&fresh=true" : ""}`).then((d) => {
      const open = d.leagues.filter(pickable).map((l) => l.key);
      setError(null);
      setData(d);
      setChecked(open);
      setDef(hasLeagues ? null : open[0] ?? null);   // a first run opens one of them; a later add keeps the default
    }, (e) => setError(why(e))).finally(() => setLoading(false)), [account, hasLeagues]);

  useEffect(() => { void read(false); }, [read]);
  const load = (fresh: boolean) => { setLoading(true); setError(null); void read(fresh); };

  function toggle(key: string, on: boolean) {
    const next = on ? [...checked, key] : checked.filter((k) => k !== key);
    setChecked(next);
    if (!on && def === key) setDef(hasLeagues ? null : next[0] ?? null);
    if (on && !def && !hasLeagues) setDef(key);
  }

  if (loading && !data) {
    return (
      <>
        <Heading title="Your leagues">Reading your leagues from ESPN…</Heading>
        <p className="flex items-center gap-2 text-sm text-muted-foreground"><Spinner on gap="" />A few seconds</p>
      </>
    );
  }
  if (error && !data) {
    return (
      <>
        <Heading title="Your leagues">{error}</Heading>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => load(true)} className={PHONE_ACTION}>Try again</Button>
          <Button variant="outline" onClick={onBack} className={PHONE_ACTION}>Back</Button>
        </div>
      </>
    );
  }
  const leagues = data?.leagues ?? [];
  const chosen = leagues.filter((l) => checked.includes(l.key));
  const n = chosen.length;
  return (
    <>
      <Heading title="Your leagues">
        Every {data?.season} football league on the ESPN login you just connected. Pick the ones FFL should follow.
      </Heading>
      {leagues.length === 0 ? (
        <p className="text-sm text-muted-foreground">ESPN lists no football league on this login this season.</p>
      ) : (
        <Card className="py-0">
          <CardContent className="divide-y divide-foreground/8 px-0">
            {leagues.map((l) => (
              <LeagueRow key={l.key} league={l} checked={l.joined || checked.includes(l.key)}
                         onCheck={(on) => toggle(l.key, on)} isDefault={def === l.key}
                         onDefault={pickable(l) && checked.includes(l.key) && def !== l.key ? () => setDef(l.key) : undefined} />
            ))}
          </CardContent>
        </Card>
      )}
      <p className="text-xs leading-4 text-muted-foreground">
        Not here? FFL follows ESPN football only, this season. A league you joined minutes ago can take a while to
        show:{" "}
        <button type="button" onClick={() => load(true)} disabled={loading}
                className="underline underline-offset-4 hover:text-foreground disabled:opacity-50">check again</button>.
      </p>
      <Button onClick={() => onPicked(chosen, def)} disabled={n === 0} className={`w-full ${PHONE_ACTION}`}>
        {n === 0 ? "Pick a league" : `Add ${n} league${n === 1 ? "" : "s"}`}
      </Button>
    </>
  );
}

function LeagueRow({ league: l, checked, onCheck, isDefault, onDefault }: {
  league: LeagueChoice; checked: boolean; onCheck: (on: boolean) => void; isDefault: boolean; onDefault?: () => void;
}) {
  const id = `pick-${l.league_id}`;
  const dim = !pickable(l);
  const sub = l.joined ? "Already in FFL" : [l.team, l.size ? `${l.size} teams` : null].filter(Boolean).join(" · ");
  return (
    <Item size="sm" className="rounded-none px-4 py-3.5">
      <Checkbox id={id} checked={checked} disabled={dim} onCheckedChange={(v) => onCheck(v === true)} className="size-5" />
      <ItemMedia><TeamMark logo={l.logo} team={l.team ?? l.name} dim={dim} /></ItemMedia>
      <ItemContent className={`min-w-0 gap-0.5 ${dim ? "opacity-60" : ""}`}>
        <ItemTitle className="flex flex-wrap items-center gap-2">
          <label htmlFor={id} className="font-display text-xl font-semibold leading-tight">{l.name}</label>
          {isDefault && (
            <span className="font-display inline-flex h-5 items-center rounded-sm bg-foreground/10 px-1.5 text-[0.6875rem] font-semibold uppercase tracking-[0.12em] text-foreground/75">
              Opens first
            </span>
          )}
        </ItemTitle>
        <ItemDescription className="text-[13px]">{sub}</ItemDescription>
      </ItemContent>
      <ItemActions>
        {l.problem && <LeagueProblemMark problem={l.problem} league={l.name} />}
        {onDefault && (
          <Button variant="link" size="sm" onClick={onDefault} className="h-8 px-2 text-muted-foreground">Open first</Button>
        )}
      </ItemActions>
    </Item>
  );
}

// --- 3 Set up ---

type Run = { state: "waiting" | "working" | "ready" | "failed"; message?: string };

function SetupStep({ leagues, def, onBack }: { leagues: LeagueChoice[]; def: string | null; onBack: () => void }) {
  const [runs, setRuns] = useState<Record<string, Run>>(() => Object.fromEntries(leagues.map((l) => [l.key, { state: "waiting" }])));
  const started = useRef(false);

  const run = useCallback(async (l: LeagueChoice) => {
    setRuns((r) => ({ ...r, [l.key]: { state: "working" } }));
    try {
      const res = await espnPost<{ leagues: PickResult[] }>(
        "/espn/leagues", { ids: [l.league_id], default: def === l.key ? l.league_id : null }, 90_000);
      const one = res.leagues[0];
      setRuns((r) => ({ ...r, [l.key]: one?.ok ? { state: "ready" } : { state: "failed", message: one?.message } }));
    } catch (e) {
      setRuns((r) => ({ ...r, [l.key]: { state: "failed", message: why(e) } }));
    }
  }, [def]);

  // one league at a time, the default first, so the page can open as soon as it is ready
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const order = [...leagues].sort((a, b) => Number(b.key === def) - Number(a.key === def));
    (async () => { for (const l of order) await run(l); })();
  }, [leagues, def, run]);

  const ready = leagues.filter((l) => runs[l.key]?.state === "ready");
  const busy = leagues.some((l) => ["waiting", "working"].includes(runs[l.key]?.state));
  const open = ready.find((l) => l.key === def) ?? ready[0];
  return (
    <>
      <Heading title="Setting up">FFL reads each league&apos;s scoring and rosters from ESPN. A few seconds a league.</Heading>
      <Card className="py-0">
        <CardContent className="divide-y divide-foreground/8 px-0">
          {leagues.map((l) => {
            const r = runs[l.key];
            return (
              <Item key={l.key} size="sm" className="rounded-none px-4 py-3.5">
                <ItemMedia><TeamMark logo={l.logo} team={l.team ?? l.name} /></ItemMedia>
                <ItemContent className="min-w-0 gap-1">
                  <ItemTitle className="font-display text-xl font-semibold leading-tight">{l.name}</ItemTitle>
                  <ItemDescription className="text-[13px]" aria-live="polite">
                    {r.state === "ready" ? "Ready" : r.state === "failed" ? (r.message ?? "This league couldn't be set up.")
                      : r.state === "working" ? "Reading settings…" : "Waiting"}
                  </ItemDescription>
                  {r.state === "failed" && (
                    <Button variant="outline" onClick={() => run(l)} className="mt-1 h-9 self-start">Try again</Button>
                  )}
                </ItemContent>
                <ItemActions>
                  {r.state === "ready" && <Check className="size-5" aria-label="Ready" />}
                  {r.state === "working" && <LoaderCircle className="size-5 animate-spin text-muted-foreground" aria-label="Working" />}
                </ItemActions>
              </Item>
            );
          })}
        </CardContent>
      </Card>
      {busy ? (
        // the leagues are set up from this page, one at a time: leaving it would stop the rest
        <Button disabled className={`w-full ${PHONE_ACTION}`}><Spinner on />Setting up…</Button>
      ) : open ? (
        // a full load: the layout reads the leagues (the switcher) once per document
        <Button className={`w-full ${PHONE_ACTION}`} render={<a href={`/roster?league=${encodeURIComponent(open.key)}`} />} nativeButton={false}>
          Open {open.name}
        </Button>
      ) : (
        <Button variant="outline" onClick={onBack} className={`w-full ${PHONE_ACTION}`}>Back to your leagues</Button>
      )}
    </>
  );
}
