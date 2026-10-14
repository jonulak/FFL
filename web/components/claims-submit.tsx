"use client";

import { Check, ChevronLeft, FlaskConical, TriangleAlert, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import type { Claim, Presets, SubmitResult } from "@/lib/api";
import { deferred, savedNote, whyRows } from "@/lib/decisions";
import { atMost, errorWhere, outsideWhere, processDay, submitSummary, submitTimeoutMs, type SubmitFailure } from "@/lib/claims";
import { ClaimName } from "./claim-name";
import { SubmitError, useClaims } from "./claims-context";
import { useDecisionPrefs } from "./decision-prefs";
import { RationaleForm, usePresets } from "./rationale-form";
import { Spinner, Swap } from "./motion";

const SUB = "font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground";

// Phone action bars sit on the screen's bottom edge. The layout sets no viewport-fit=cover, so in
// the in-app browser env(safe-area-inset-bottom) reads 0 and a max() with it alone left the
// buttons in the home-indicator gesture zone: a fixed floor clears it, the inset adds to it when real.
export const PHONE_INSET = "pb-[max(1.75rem,calc(env(safe-area-inset-bottom)+0.75rem))]";
// A phone action: a full thumb's height, not the 32px desk button.
export const PHONE_ACTION = "max-lg:h-12 max-lg:px-5 max-lg:text-base";

// The test server's banner (FFL_WRITES=0): every submit goes to an in-memory fake, so nothing the
// panel or the dialog says about a claim is about ESPN. Shown wherever a send can be made or read.
export function TestModeAlert({ className = "" }: { className?: string }) {
  return (
    <Alert className={`border-close/60 bg-close/10 text-foreground ${className}`}>
      <FlaskConical aria-hidden />
      <AlertTitle>Test mode — nothing is sent to ESPN</AlertTitle>
      <AlertDescription>Claims go to this server&apos;s in-memory fake (FFL_WRITES=0). Its answers are not ESPN&apos;s.</AlertDescription>
    </Alert>
  );
}

// "+ Addison  − Shaheed" (or "open spot"): one move as the dialog lists it.
function MoveLine({ m, n }: { m: Pick<Claim, "add" | "drop">; n?: number }) {
  return (
    <li className="grid grid-cols-[1.5rem_minmax(0,1fr)_minmax(0,1fr)] items-baseline gap-x-2 border-t border-foreground/8 py-1 first:border-t-0">
      <span className="text-right font-display font-semibold tabular-nums text-muted-foreground">{n ?? ""}</span>
      <span className="break-words font-medium">+ <ClaimName id={m.add} /></span>
      {m.drop == null
        ? <span className="text-muted-foreground">open spot</span>
        : <span className="break-words text-out">− <ClaimName id={m.drop} /></span>}
    </li>
  );
}

function Verdict({ ok, children }: { ok: boolean; children: ReactNode }) {
  const Icon = ok ? Check : TriangleAlert;
  return (
    <p className={`flex items-start gap-1.5 font-medium ${ok ? "" : "text-close-foreground dark:text-close"}`}>
      <Icon aria-hidden className="mt-0.5 size-4 shrink-0 stroke-[2.5]" />
      <span>{children}</span>
    </p>
  );
}

function Failure({ f, name, writes }: { f: SubmitFailure; name: (id: number) => string; writes: boolean }) {
  const who = writes ? "ESPN" : "the fake";
  return (
    <div role="alert" className="space-y-1 rounded-md bg-destructive/10 px-3 py-2 text-destructive">
      {f.kind === "running" && <p>A submit is already running. Wait for it, then look at what {who} holds before sending again.</p>}
      {f.kind === "espn" && <p>ESPN refused: {f.error}. Nothing was sent.</p>}
      {f.kind === "changed" && <p>Nothing was sent: {f.error}. The list below is what {who} says now.</p>}
      {f.kind === "other" && <p>{f.error}</p>}
      {f.kind === "invalid" && (
        <>
          <p className="font-medium">Nothing was sent: the plan has problems.</p>
          <ul className="list-disc pl-5">
            {f.errors.map((e, i) => <li key={i}>{errorWhere(e)}{e.player_id != null && `, ${name(e.player_id)}`}: {e.reason}</li>)}
          </ul>
        </>
      )}
    </div>
  );
}

// What came back: the instant adds made and refused, then the claims ESPN holds against what was sent.
function Outcome({ r, verdict, name, writes }: { r: SubmitResult; verdict: { ok: boolean; text: string }; name: (id: number) => string; writes: boolean }) {
  const who = writes ? "ESPN" : "the fake";
  return (
    <div className="space-y-3" aria-live="polite">
      {(r.now.length > 0 || r.now_failed.length > 0) && (
        <section className="space-y-1">
          <h3 className={SUB}>Added now</h3>
          {r.now.map((m) => <Verdict key={m.add} ok>added {name(m.add)}{m.drop != null && <span className="font-normal text-muted-foreground">, dropped {name(m.drop)}</span>}</Verdict>)}
          {r.now_failed.map((m) => (
            <p key={m.add} className="flex items-start gap-1.5 text-destructive">
              <X aria-hidden className="mt-0.5 size-4 shrink-0 stroke-[2.5]" />
              <span><span className="font-medium">{name(m.add)}</span>: {m.error}. His row went to the claims.</span>
            </p>
          ))}
        </section>
      )}
      <section className="space-y-1">
        <h3 className={SUB}>Claims</h3>
        <Verdict ok={verdict.ok}>{verdict.text}</Verdict>
        {r.error && <p className="text-destructive">{r.error}</p>}
        {!verdict.ok && !r.readback_failed && (
          <>
            <p className="pt-1 text-muted-foreground">{writes ? "ESPN's" : "The fake's"} list, in its order:</p>
            {r.pending.length
              ? <ol>{r.pending.map((c, i) => <MoveLine key={c.id} m={c} n={i + 1} />)}</ol>
              : <p className="text-muted-foreground">nothing pending</p>}
          </>
        )}
        {r.readback_failed && (
          <p className="text-muted-foreground">Check the claims {writes ? "on ESPN's site" : `held by ${who}`}, or review and send again: a resend cancels what this app sent before.</p>
        )}
      </section>
    </div>
  );
}

// One reason for the whole submit, asked from the moment Send is pressed rather than after ESPN's minute: the rows
// arrive with the answer (`targets`, see whyRows), the first takes the form and the others the same reason.
// No Later here; Done leaves them open on the Decisions page.
function ClaimsWhy({ targets, presets }: { targets: Promise<number[]>; presets: Presets | null }) {
  const [saved, setSaved] = useState<string | null>(null);
  if (!presets) return null;
  return (
    <section className="space-y-1.5">
      <h3 className={SUB}>Why these claims?</h3>
      {saved
        ? <p className="log-in text-sm text-muted-foreground">{saved}</p>
        // kind "claims" even when the first row is a free add: the two kinds offer the same reasons
        : <RationaleForm decision={{ kind: "claims" }} targets={targets} presets={presets}
            onSaved={(_, failed) => setSaved(savedNote(failed))} />}
    </section>
  );
}

// withdrawn: the claims an empty plan was sent to cancel (ours, plus the outside ones when the box was ticked)
type Frozen = { title: string; planned: number; outside: Set<string>; withdrawn: number };

// The review: what will be sent, then what ESPN held. The desk's dialog hosts it, and so does the
// phone's claims sheet as its second step (`phone`: Back to the claims, the phone's footer at
// every width below lg). `titled` is false while the sheet fades it out, so one title labels the sheet.
export function ReviewBody({ onClose, onBack, phone = false, titled = true }: { onClose: () => void; onBack?: () => void; phone?: boolean; titled?: boolean }) {
  const { data, groups, player, status, submit, submitting, refresh } = useClaims();
  const router = useRouter();
  const presets = usePresets();
  const { log } = useDecisionPrefs();
  const [replaceOutside, setReplaceOutside] = useState(false);
  const [result, setResult] = useState<SubmitResult | null>(null);
  const [fault, setFault] = useState<SubmitFailure | null>(null);
  const name = (id: number) => player(id)?.name ?? `player ${id}`;
  const line = (c: Pick<Claim, "add" | "drop">) => `+${name(c.add)}${c.drop != null ? ` −${name(c.drop)}` : ""}`;
  const fromApp = data.pending.filter((c) => c.from_app);
  const outside = data.pending.filter((c) => !c.from_app);
  const pos = new Map(Object.entries(data.players).map(([k, p]) => [Number(k), p.pos]));
  const day = processDay(groups.flatMap((g) => g.targets).filter((t) => status[String(t)] !== "FREEAGENT").map((t) => player(t)?.waiver_date ?? null));
  const { writes } = data;
  const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;
  // An empty plan withdraws: it cancels the claims sent from here (and the outside ones with the box
  // ticked) and posts nothing. With nothing to cancel there is nothing to send.
  const withdrawal = data.now.length === 0 && data.claims.length === 0;
  const withdrawn = withdrawal ? fromApp.length + (replaceOutside ? outside.length : 0) : 0;
  const blocked = data.errors.length > 0 || (withdrawal && withdrawn === 0);
  const title = withdrawal
    ? `Withdraw ${plural(withdrawn, "claim")}${writes ? " from ESPN" : ""}`
    : data.now.length
      ? `Add ${data.now.length} now, send ${plural(data.claims.length, "claim")}`
      : `Send ${plural(data.claims.length, "claim")}${writes ? " to ESPN" : ""}`;

  // The plan as it stood when Send was pressed: the title, the claim count the verdict is judged
  // against, and the outside claims kept. The plan refreshes after a send (landed adds leave it),
  // and the result must be read against what was sent, not against the refreshed plan.
  const [frozen, setFrozen] = useState<Frozen | null>(null);
  // Statuses move (the Wednesday run turns waivers into free agents): re-read the plan on open so
  // the "Add now" list is what the server will run (Send passes it, and the server refuses a
  // different one). Send waits for it.
  const [loading, setLoading] = useState(true);
  useEffect(() => { void refresh(true).finally(() => setLoading(false)); }, [refresh]);

  // The reason form, up while the submit runs: `rows` settles with the answer's rows (whyRows), `needed` says
  // whether any of them still wants a reason (null until the answer), and the form leaves when none does.
  const [why, setWhy] = useState<{ rows: ReturnType<typeof deferred<number[]>>; needed: boolean | null } | null>(null);
  const send = async () => {
    setFault(null);
    setFrozen({ title, planned: data.claims.length, outside: replaceOutside ? new Set() : new Set(outside.map((c) => c.id)), withdrawn });
    const rows = deferred<number[]>();
    // with logging off in Settings the server records nothing, so the form never opens
    if (log) setWhy({ rows, needed: null });
    try {
      // the wait grows with the plan: one ESPN call per cancel, instant add and claim, in turn
      const cancels = replaceOutside ? data.pending.length : fromApp.length;
      const r = await submit(replaceOutside, data.now, submitTimeoutMs(cancels, data.now.length + data.claims.length));
      const w = whyRows(r.decisions ?? []);
      rows.resolve(w.ids);
      setWhy(log && w.needed ? { rows, needed: true } : null);
      setResult(r);
      if (r.now.length) router.refresh(); // the roster changed: the table's comparisons and the pool
    } catch (e) {
      rows.reject(e);
      setWhy(null);
      const f: SubmitFailure = e instanceof SubmitError ? e.failure : { kind: "other", error: e instanceof Error ? e.message : String(e) };
      setFault(f);
      if (f.kind === "changed") {
        // statuses or ESPN's week moved since this review: back to it, re-read, with the reason showing
        setFrozen(null);
        setLoading(true);
        await refresh(true);
        setLoading(false);
      }
    }
  };
  // No one-click resend: back to the review, over the refreshed plan (its "Add now" included).
  const reviewAgain = async () => {
    setLoading(true);
    await refresh(true);
    setResult(null);
    setFault(null);
    setFrozen(null);
    setWhy(null);
    setLoading(false);
  };

  const Title = titled ? DialogTitle : "h2";
  const verdict = result && frozen
    ? submitSummary(result, frozen.planned, result.pending.filter((c) => frozen.outside.has(c.id)).length, { withdrawn: frozen.withdrawn, writes })
    : null;
  const resend = verdict && !verdict.ok;
  return (
    <>
      <DialogHeader className={phone ? "pr-9" : "pr-8"}>
        <div className="flex items-center gap-1">
          {onBack && (
            <Button variant="ghost" size="icon-sm" aria-label="Back to the claims" disabled={submitting} onClick={onBack}
              className="relative -ml-2 shrink-0 pointer-coarse:after:absolute pointer-coarse:after:-inset-1.5">
              <ChevronLeft aria-hidden className="size-5" />
            </Button>
          )}
          <Title className="font-display text-2xl font-bold leading-tight tracking-tight">{(result || submitting) && frozen ? frozen.title : title}</Title>
        </div>
        {titled && (
          <DialogDescription className="sr-only">
            {withdrawal ? "Confirm the withdrawal of the claims sent from here." : `Review the moves, then send them${writes ? " to ESPN" : ""}.`}
          </DialogDescription>
        )}
      </DialogHeader>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain">
        {!writes && <TestModeAlert />}
        {!result && data.now.length > 0 && (
          <section className="space-y-1.5">
            <h3 className={SUB}>Add now · instant, can&apos;t be undone</h3>
            <ol>{data.now.map((m) => <MoveLine key={m.add} m={m} />)}</ol>
            <p className="text-xs leading-relaxed text-muted-foreground">
              These happen the moment you press Send; if one is taken, his cut moves down to the claims.
            </p>
          </section>
        )}
        {!result && (
          <section className="space-y-1.5">
            <h3 className={SUB}>{withdrawal ? "Withdraw" : `Claims · process ${day}`}</h3>
            {withdrawal && withdrawn > 0 && (
              <p className="text-sm leading-relaxed">
                This withdraws
                {fromApp.length > 0 && <> the {plural(fromApp.length, "claim")} sent from here ({fromApp.map(line).join(", ")})</>}
                {fromApp.length > 0 && replaceOutside && " and"}
                {replaceOutside && <> the {plural(outside.length, "claim")} {outsideWhere(writes)} ({outside.map(line).join(", ")})</>}.
                {" "}{writes ? "ESPN" : "The fake"} will hold none of yours{outside.length > 0 && !replaceOutside ? `; the ${plural(outside.length, "claim")} ${outsideWhere(writes)} stay${outside.length === 1 ? "s" : ""}` : ""}.
              </p>
            )}
            {!withdrawal && fromApp.length > 0 && (
              <p className="text-xs leading-relaxed text-muted-foreground">
                This replaces the {plural(fromApp.length, "claim")} sent from here.
              </p>
            )}
            {outside.length > 0 && (
              <div className="space-y-1.5 rounded-md bg-foreground/[0.04] px-3 py-2">
                <p className="text-xs leading-relaxed">
                  <span className="font-medium">{outside.length} pending claim{outside.length === 1 ? " was" : "s were"} {outsideWhere(writes)}, not here:</span>{" "}
                  {outside.map(line).join(", ")}.
                </p>
                <Label className="text-xs font-normal">
                  <Checkbox checked={replaceOutside} onCheckedChange={setReplaceOutside} disabled={submitting} />
                  {withdrawal ? "withdraw" : "replace"} {outside.length === 1 ? "that one" : "these"} too
                </Label>
              </div>
            )}
            {withdrawal
              ? null
              : data.claims.length
                ? <ol>{data.claims.map((m, i) => <MoveLine key={`${m.add}:${m.drop}`} m={m} n={i + 1} />)}</ol>
                : <p className="text-muted-foreground">No claims: every target is a free agent.</p>}
            {atMost(groups, pos) && <p className="text-xs text-muted-foreground">{atMost(groups, pos)}.</p>}
          </section>
        )}
        {/* ESPN's answer rises into the room the review leaves */}
        {result && verdict && <div className="log-in"><Outcome r={result} verdict={verdict} name={name} writes={writes} /></div>}
        {/* the read on open can turn up problems the panel didn't have (a status moved): Send waits for a fix */}
        {!result && !loading && data.errors.length > 0 && (
          <div role="alert" className="log-in space-y-1 rounded-md bg-destructive/10 px-3 py-2 text-destructive">
            <p className="font-medium">Can&apos;t send: the plan has problems. Fix them in the claims.</p>
            <ul className="list-disc pl-5">
              {data.errors.map((e, i) => <li key={i}>{errorWhere(e)}{e.player_id != null && `, ${name(e.player_id)}`}: {e.reason}</li>)}
            </ul>
          </div>
        )}
        {!result && !loading && withdrawal && withdrawn === 0 && (
          <p className="text-muted-foreground">Nothing to withdraw: no claim sent from here is pending.</p>
        )}
        {why && <div className="log-in" style={{ "--i": 1 } as CSSProperties}><ClaimsWhy targets={why.rows.promise} presets={presets} /></div>}
        {fault && <div className="log-in"><Failure f={fault} name={name} writes={writes} /></div>}
      </div>
      {/* PHONE_INSET, written out: Tailwind only generates a variant it can read in the source */}
      <DialogFooter className={phone ? `mb-0 gap-3 rounded-none px-5 pt-3 ${PHONE_INSET}` : "gap-2 max-sm:rounded-none max-sm:gap-3 max-sm:px-5 max-sm:pt-3 max-sm:pb-[max(1.75rem,calc(env(safe-area-inset-bottom)+0.75rem))]"}>
        {result && <Button variant={resend ? "outline" : "default"} onClick={onClose} className={`fx-in ${PHONE_ACTION}`}>Done</Button>}
        {resend && (
          <Button onClick={reviewAgain} disabled={loading} className={`fx-in min-w-24 ${PHONE_ACTION}`}>
            <Spinner on={loading} />
            Review again
          </Button>
        )}
        {!result && (
          <Button onClick={send} disabled={submitting || loading || blocked} className={`min-w-24 ${PHONE_ACTION}`}>
            <Spinner on={submitting || loading} />
            <Swap>{submitting ? (withdrawal ? "Withdrawing…" : "Sending…") : loading ? `Checking ${writes ? "ESPN" : "the fake"}…` : withdrawal ? "Withdraw" : "Send"}</Swap>
          </Button>
        )}
      </DialogFooter>
    </>
  );
}

// The Submit dialog: exactly what will be sent (instant adds first, then the numbered claims), then
// what ESPN ended up holding. Full screen on a phone. Closing is refused while a send runs.
export function ClaimsSubmit({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { submitting } = useClaims();
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!submitting) onOpenChange(o); }}>
      <DialogContent showCloseButton={!submitting}
        className="flex max-h-[calc(var(--vv-h,100dvh)-4rem)] flex-col gap-4 rounded-lg shadow-[0_24px_48px_-20px_rgb(0_0_0_/_0.6)] sm:max-w-lg max-sm:h-[var(--vv-h,100dvh)] max-sm:max-h-[var(--vv-h,100dvh)] max-sm:max-w-none max-sm:rounded-none">
        {open && <ReviewBody onClose={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  );
}
