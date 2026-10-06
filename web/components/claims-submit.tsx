"use client";

import { Check, LoaderCircle, TriangleAlert, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import type { Claim, SubmitResult } from "@/lib/api";
import { atMost, processDay, submitSummary } from "@/lib/claims";
import { SubmitError, useClaims, type SubmitFailure } from "./claims-context";

const SUB = "font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground";

// "+ Addison  − Shaheed" (or "open spot"): one move as the dialog lists it.
function MoveLine({ m, name, n }: { m: Pick<Claim, "add" | "drop">; name: (id: number) => string; n?: number }) {
  return (
    <li className="grid grid-cols-[1.5rem_minmax(0,1fr)_minmax(0,1fr)] items-baseline gap-x-2 border-t border-foreground/8 py-1 first:border-t-0">
      <span className="text-right font-display font-semibold tabular-nums text-muted-foreground">{n ?? ""}</span>
      <span className="truncate font-medium">+ {name(m.add)}</span>
      {m.drop == null
        ? <span className="truncate text-muted-foreground">open spot</span>
        : <span className="truncate text-out">− {name(m.drop)}</span>}
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

function Failure({ f, name }: { f: SubmitFailure; name: (id: number) => string }) {
  return (
    <div role="alert" className="space-y-1 rounded-md bg-destructive/10 px-3 py-2 text-destructive">
      {f.kind === "running" && <p>A submit is already running. Wait for it, then look at what ESPN holds before sending again.</p>}
      {f.kind === "espn" && <p>ESPN refused: {f.error}. Nothing was sent.</p>}
      {f.kind === "other" && <p>{f.error}</p>}
      {f.kind === "invalid" && (
        <>
          <p className="font-medium">Nothing was sent: the plan has problems.</p>
          <ul className="list-disc pl-5">
            {f.errors.map((e, i) => <li key={i}>Group {e.group + 1}{e.player_id != null && `, ${name(e.player_id)}`}: {e.reason}</li>)}
          </ul>
        </>
      )}
    </div>
  );
}

// What came back: the instant adds made and refused, then the claims ESPN holds against what was sent.
function Outcome({ r, verdict, name }: { r: SubmitResult; verdict: { ok: boolean; text: string }; name: (id: number) => string }) {
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
            <p className="pt-1 text-muted-foreground">ESPN&apos;s list, in its order:</p>
            {r.pending.length
              ? <ol>{r.pending.map((c, i) => <MoveLine key={c.id} m={c} name={name} n={i + 1} />)}</ol>
              : <p className="text-muted-foreground">nothing pending</p>}
          </>
        )}
        {r.readback_failed && (
          <p className="text-muted-foreground">Check the claims on ESPN&apos;s site, or review and send again: a resend cancels what this app sent before.</p>
        )}
      </section>
    </div>
  );
}

type Frozen = { title: string; planned: number; outside: Set<string> };

function Body({ onClose }: { onClose: () => void }) {
  const { data, groups, player, status, submit, submitting, refresh } = useClaims();
  const router = useRouter();
  const [replaceOutside, setReplaceOutside] = useState(false);
  const [result, setResult] = useState<SubmitResult | null>(null);
  const [fault, setFault] = useState<SubmitFailure | null>(null);
  const name = (id: number) => player(id)?.name ?? `player ${id}`;
  const fromApp = data.pending.filter((c) => c.from_app);
  const outside = data.pending.filter((c) => !c.from_app);
  const pos = new Map(Object.entries(data.players).map(([k, p]) => [Number(k), p.pos]));
  const day = processDay(groups.flatMap((g) => g.targets).filter((t) => status[String(t)] !== "FREEAGENT").map((t) => player(t)?.waiver_date ?? null));
  const title = data.now.length
    ? `Add ${data.now.length} now, send ${data.claims.length} claim${data.claims.length === 1 ? "" : "s"}`
    : `Send ${data.claims.length} claim${data.claims.length === 1 ? "" : "s"} to ESPN`;

  // The plan as it stood when Send was pressed: the title, the claim count the verdict is judged
  // against, and the outside claims kept. The plan refreshes after a send (landed adds leave it),
  // and the result must be read against what was sent, not against the refreshed plan.
  const [frozen, setFrozen] = useState<Frozen | null>(null);
  // Statuses move (the Wednesday run turns waivers into free agents): re-read the plan on open so
  // the "Add now" list is what the server will run. Send waits for it.
  const [loading, setLoading] = useState(true);
  useEffect(() => { void refresh().finally(() => setLoading(false)); }, [refresh]);

  const send = async () => {
    setFault(null);
    setFrozen({ title, planned: data.claims.length, outside: replaceOutside ? new Set() : new Set(outside.map((c) => c.id)) });
    try {
      const r = await submit(replaceOutside);
      setResult(r);
      if (r.now.length) router.refresh(); // the roster changed: the table's comparisons and the pool
    } catch (e) {
      setFault(e instanceof SubmitError ? e.failure : { kind: "other", error: e instanceof Error ? e.message : String(e) });
    }
  };
  // No one-click resend: back to the review, over the refreshed plan (its "Add now" included).
  const reviewAgain = async () => {
    setLoading(true);
    await refresh();
    setResult(null);
    setFault(null);
    setFrozen(null);
    setLoading(false);
  };

  const verdict = result && frozen ? submitSummary(result, frozen.planned, result.pending.filter((c) => frozen.outside.has(c.id)).length) : null;
  const resend = verdict && !verdict.ok;
  return (
    <>
      <DialogHeader className="pr-8">
        <DialogTitle className="font-display text-2xl font-bold leading-tight tracking-tight">{(result || submitting) && frozen ? frozen.title : title}</DialogTitle>
        <DialogDescription className="sr-only">Review the moves, then send them to ESPN.</DialogDescription>
      </DialogHeader>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain">
        {!result && data.now.length > 0 && (
          <section className="space-y-1.5">
            <h3 className={SUB}>Add now · instant, can&apos;t be undone</h3>
            <ol>{data.now.map((m) => <MoveLine key={m.add} m={m} name={name} />)}</ol>
            <p className="text-xs leading-relaxed text-muted-foreground">
              These happen the moment you press Send; if one is taken, his cut moves down to the claims.
            </p>
          </section>
        )}
        {!result && (
          <section className="space-y-1.5">
            <h3 className={SUB}>Claims · process {day}</h3>
            {fromApp.length > 0 && (
              <p className="text-xs leading-relaxed text-muted-foreground">
                This replaces the {fromApp.length} claim{fromApp.length === 1 ? "" : "s"} sent from here.
              </p>
            )}
            {outside.length > 0 && (
              <div className="space-y-1.5 rounded-md bg-foreground/[0.04] px-3 py-2">
                <p className="text-xs leading-relaxed">
                  <span className="font-medium">{outside.length} pending claim{outside.length === 1 ? " was" : "s were"} filed on ESPN, not here:</span>{" "}
                  {outside.map((c) => `+${name(c.add)}${c.drop != null ? ` −${name(c.drop)}` : ""}`).join(", ")}.
                </p>
                <Label className="text-xs font-normal">
                  <Checkbox checked={replaceOutside} onCheckedChange={setReplaceOutside} disabled={submitting} />
                  replace {outside.length === 1 ? "that one" : "these"} too
                </Label>
              </div>
            )}
            {data.claims.length
              ? <ol>{data.claims.map((m, i) => <MoveLine key={`${m.add}:${m.drop}`} m={m} name={name} n={i + 1} />)}</ol>
              : <p className="text-muted-foreground">No claims: every target is a free agent.</p>}
            {atMost(groups, pos) && <p className="text-xs text-muted-foreground">{atMost(groups, pos)}.</p>}
          </section>
        )}
        {result && verdict && <Outcome r={result} verdict={verdict} name={name} />}
        {fault && <Failure f={fault} name={name} />}
      </div>
      <DialogFooter className="gap-2 max-sm:rounded-none max-sm:pb-[max(1rem,env(safe-area-inset-bottom))]">
        {result && <Button variant={resend ? "outline" : "default"} onClick={onClose}>Done</Button>}
        {resend && (
          <Button onClick={reviewAgain} disabled={loading} className="min-w-24">
            {loading && <LoaderCircle aria-hidden className="animate-spin" />}
            Review again
          </Button>
        )}
        {!result && (
          <Button onClick={send} disabled={submitting || loading} className="min-w-24">
            {(submitting || loading) && <LoaderCircle aria-hidden className="animate-spin" />}
            {submitting ? "Sending…" : loading ? "Checking ESPN…" : "Send"}
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
        className="flex max-h-[calc(100dvh-4rem)] flex-col gap-4 rounded-lg shadow-[0_24px_48px_-20px_rgb(0_0_0_/_0.6)] sm:max-w-lg max-sm:h-dvh max-sm:max-h-dvh max-sm:max-w-none max-sm:rounded-none">
        {open && <Body onClose={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  );
}
