"use client";

import { ArrowLeftRight } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { clientGet, clientPost } from "@/app/api-client";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Item } from "@/components/ui/item";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { Trade, TradePlayer, TradeSide } from "@/lib/api";
import { HEALTHY, LABEL, STATUS_SHORT, TONE } from "@/lib/board";
import { injuryTone, shortName, signed } from "@/lib/format";
import { holeLines, marketDelta, marketSides, marketValue, tradePath } from "@/lib/trade";
import { PHONE_ACTION, PHONE_INSET } from "./claims-submit";
import { Spinner, Ticker } from "./motion";
import { TeamPanel } from "./player-media";
import { StaleBadge } from "./stale-badge";
import { useDesk } from "./swap-sheet";

// Price a trade with another team: tick who moves on either side and read what it does to both starting lineups. It
// prices and never opines, and nothing reaches ESPN. The price stands above the rosters, so it stays in view while the
// thumb works down a list.

const delta = (n: number) => signed(n, 1);
const flip = (ids: number[], id: number) => (ids.includes(id) ? ids.filter((i) => i !== id) : [...ids, id]);
const ROSTER = "-mx-2 min-h-0 flex-1 overflow-y-auto overscroll-contain";

function Change({ n, format, unit, from, to, end }: { n: number; format: (n: number) => string; unit: string; from: string; to: string; end?: boolean }) {
  return (
    <>
      <p className={`mt-1 flex items-baseline gap-1 font-display tabular-nums ${end ? "justify-end" : ""}`}>
        {/* a loss steps back to 70% ink, as in the play feed: no verdict colour says who won */}
        <span className={`text-[2rem] font-bold leading-none tracking-tight ${n < 0 ? "text-foreground/70" : ""}`}>
          {/* answers a tap, so it can't take the routine change's time: these jumps would all hit its 900ms cap */}
          <Ticker value={n} format={format} duration={250} />
        </span>
        <span className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">{unit}</span>
      </p>
      <p className="mt-1 whitespace-nowrap text-xs tabular-nums text-muted-foreground">{from} → {to}</p>
    </>
  );
}

function SidePrice({ label, s, market, end }: { label: string; s: TradeSide; market?: { delta: number; before: number; after: number }; end?: boolean }) {
  const notes = [...holeLines(s.holes), ...s.cut.map((c) => `Would cut ${c.name}`)];
  return (
    <div className={`min-w-0 ${end ? "text-right" : ""}`}>
      <p className={LABEL}>{label}</p>
      <Change n={s.delta} format={delta} unit="pts" from={s.before.toFixed(1)} to={s.after.toFixed(1)} end={end} />
      {market && <div className="mt-2"><Change n={market.delta} format={marketDelta} unit="mkt" from={marketValue(market.before)} to={marketValue(market.after)} end={end} /></div>}
      {notes.map((n) => <p key={n} className="mt-1 text-xs text-pretty text-foreground/85">{n}</p>)}
    </div>
  );
}

function RowNumber({ value, unit }: { value: string; unit: string }) {
  return (
    <span className="w-12 text-right font-display tabular-nums">
      <span className="block text-lg font-semibold leading-none">{value}</span>
      <span className="mt-1 block text-[0.6875rem] font-semibold uppercase leading-none tracking-[0.12em] text-muted-foreground">{unit}</span>
    </span>
  );
}

function PlayerRow({ p, on, flip, week, market }: { p: TradePlayer; on: boolean; flip: () => void; week: number; market: boolean }) {
  const status = p.injury.replaceAll("_", " ");
  const hurt = !HEALTHY.has(p.injury);
  return (
    <li>
      <Item size="xs" render={<label />} className={`grid min-h-14 w-full cursor-pointer grid-cols-[1.25rem_2.5rem_minmax(0,1fr)_auto] items-center gap-x-2.5 rounded-md border-0 px-2 py-2 transition-colors hover:bg-foreground/[0.05] md:min-h-12 ${on ? "bg-foreground/[0.07]" : ""}`}>
        <Checkbox checked={on} onCheckedChange={flip} />
        <span className="block size-10 overflow-hidden rounded-md"><TeamPanel abbr={p.team} playerId={p.player_id} name={p.name} muted={false} tile /></span>
        <span className="min-w-0">
          <span className="flex flex-wrap items-baseline gap-x-1.5 font-display text-[1.0625rem] font-semibold leading-tight tracking-tight">
            <span className="min-w-0 break-words md:hidden">{shortName(p.name)}</span>
            <span className="min-w-0 break-words max-md:hidden">{p.name}</span>
            {hurt && <span title={status} className={`font-bold ${TONE[injuryTone(p.injury)]}`}>{STATUS_SHORT[status] ?? status}</span>}
          </span>
          <span className="mt-0.5 flex flex-wrap gap-x-1.5 text-xs tabular-nums text-muted-foreground">
            <span className="font-semibold text-foreground/80">{p.pos}</span>
            {p.bye_week != null && p.bye_week >= week && <span className="whitespace-nowrap">bye wk {p.bye_week}</span>}
            {hurt && p.return_week != null && p.return_week > week && <span className="whitespace-nowrap">back wk {p.return_week}</span>}
          </span>
        </span>
        <span className="flex gap-2">
          <RowNumber value={p.ros_adj.toFixed(1)} unit="ros" />
          {market && <RowNumber value={p.market == null ? "–" : marketValue(p.market)} unit="mkt" />}
        </span>
      </Item>
    </li>
  );
}

const same = (a: number[], b: number[]) => a.length === b.length && a.every((i) => b.includes(i));

// `offer`: the picks to open on (an offer from ESPN) and the button's words in place of TRADE. With `id`, an offer
// to me: the foot answers it on ESPN, as sent (`writes` false: a test server's fake takes the answer).
export function TradeSheet({ team, name, abbrev, offer, writes = true, onAnswered, className = "mt-3" }: {
  team: number; name: string; abbrev: string; offer?: { id?: string; give: number[]; get: number[]; label: string };
  writes?: boolean; onAnswered?: () => void; className?: string;
}) {
  const desk = useDesk();
  const panel = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Trade | null>(null);
  const [give, setGive] = useState<number[]>([]);
  const [get, setGet] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [confirm, setConfirm] = useState<boolean | null>(null); // the answer awaiting its second tap: true accepts
  const [answering, setAnswering] = useState(false);
  const last = useRef(0);

  async function answer(accept: boolean) {
    if (!offer?.id) return;
    setAnswering(true);
    try {
      const r = await clientPost<{ status: string }>(`/trades/${encodeURIComponent(offer.id)}/answer`, { accept });
      const done = accept ? "Accepted" : "Declined";
      toast.success(r.status === "EXECUTED" ? `${done} on ESPN` : `${done}: ESPN says ${r.status.toLowerCase()}`);
      setOpen(false);
      last.current++; // as a close does: a price still on its way is for a sheet that has gone
      onAnswered?.();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setAnswering(false);
      setConfirm(null);
    }
  }

  // only the newest ask lands: ticks can outrun the answers
  async function load(g: number[], r: number[]) {
    const mine = ++last.current;
    setBusy(true);
    setFailed(false);
    try {
      const d = await clientGet<Trade>(tradePath(team, g, r));
      if (mine === last.current) setData(d);
    } catch {
      if (mine === last.current) setFailed(true);
    } finally {
      if (mine === last.current) setBusy(false);
    }
  }

  const Title = desk ? DialogTitle : SheetTitle;
  const Description = desk ? DialogDescription : SheetDescription;
  const week = data?.ros_week ?? 0;
  // no values at all (an outage, or switched off): no column of dashes
  const market = !!data && [...data.me, ...data.them].some((p) => p.market != null);
  const sides = data?.price?.market ? marketSides(data.price.market) : undefined;
  const body = (
    <>
      <div className="pr-10">
        <Title className="flex flex-wrap items-center gap-x-2 text-balance font-display text-xl font-bold leading-tight tracking-tight">
          Trade with {name}
          {data && <StaleBadge since={data.stale_since} />}
        </Title>
        <Description className="mt-0.5 text-xs text-pretty text-muted-foreground">
          Best lineup each week through week 18. Counts byes and known absences, not future injuries or matchups.
          {offer?.id ? " Only an answer below goes to ESPN." : " Nothing goes to ESPN."}
        </Description>
      </div>
      <div aria-live="polite" className="min-h-[6.5rem] border-y border-foreground/10 py-3">
        {data?.price ? (
          <div className="grid grid-cols-2 gap-4">
            <SidePrice label="You" s={data.price.me} market={sides?.me} />
            <SidePrice label={abbrev || "Them"} s={data.price.them} market={sides?.them} end />
            {data.price.market && (
              <p className="col-span-2 text-xs text-pretty tabular-nums text-muted-foreground">
                MKT is <a href="https://fantasycalc.com" target="_blank" rel="noreferrer" className="font-medium text-foreground/85 underline underline-offset-2">FantasyCalc</a> value, traded players only
                {data.price.market.unpriced > 0 && ` · ${data.price.market.unpriced} unpriced`}
                {give.length !== get.length && give.length > 0 && get.length > 0 && ". A plain sum, so the side sending more players looks richer."}
              </p>
            )}
          </div>
        ) : failed ? null : (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            {data ? "Tick players to see what the trade does to each lineup." : "Loading both rosters"}
            <Spinner on={busy} />
          </p>
        )}
        {failed && (
          <p role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-1 pt-2 text-sm text-out">
            Couldn&apos;t read the trade from the server.
            <Button variant="outline" size="sm" onClick={() => load(give, get)}>Try again</Button>
          </p>
        )}
      </div>
      {data && (
        <Tabs defaultValue="give" className="flex min-h-0 flex-1 flex-col gap-1">
          <TabsList variant="line" indicatorInset="0.625rem" className="h-10 w-full shrink-0 justify-start gap-0 border-b border-foreground/12">
            <TabsTrigger value="give" className={`${LABEL} flex-none px-2.5 text-[0.9375rem]`}>You send {give.length > 0 && give.length}</TabsTrigger>
            <TabsTrigger value="get" className={`${LABEL} flex-none px-2.5 text-[0.9375rem]`}>You get {get.length > 0 && get.length}</TabsTrigger>
          </TabsList>
          <TabsContent value="give" className={ROSTER}>
            <ul className="space-y-0.5">
              {data.me.map((p) => (
                <PlayerRow key={p.player_id} p={p} on={give.includes(p.player_id)} week={week} market={market}
                  flip={() => { const n = flip(give, p.player_id); setGive(n); load(n, get); }} />
              ))}
            </ul>
          </TabsContent>
          <TabsContent value="get" className={ROSTER}>
            <ul className="space-y-0.5">
              {data.them.map((p) => (
                <PlayerRow key={p.player_id} p={p} on={get.includes(p.player_id)} week={week} market={market}
                  flip={() => { const n = flip(get, p.player_id); setGet(n); load(give, n); }} />
              ))}
            </ul>
          </TabsContent>
        </Tabs>
      )}
      {data && offer?.id && (
        <div className="shrink-0 space-y-2 border-t border-foreground/10 pt-3">
          {!same(give, offer.give) || !same(get, offer.get) ? (
            // ESPN takes the offer as sent: a ticked change is only a what-if, so the answer waits for the offer's picks
            <Button variant="outline" className={`w-full ${PHONE_ACTION}`}
              onClick={() => { setGive(offer.give); setGet(offer.get); load(offer.give, offer.get); }}>
              Back to the offer as sent
            </Button>
          ) : confirm === null ? (
            <div className="grid grid-cols-2 gap-2">
              <Button variant="outline" className={PHONE_ACTION} onClick={() => setConfirm(false)}>Decline</Button>
              <Button className={PHONE_ACTION} onClick={() => setConfirm(true)}>Accept</Button>
            </div>
          ) : (
            <div className="space-y-2">
              <p className="text-sm text-pretty">{confirm ? "Accept" : "Decline"} {name}&apos;s offer on ESPN? This can&apos;t be undone here.</p>
              <div className="grid grid-cols-2 gap-2">
                <Button variant="outline" className={PHONE_ACTION} disabled={answering} onClick={() => setConfirm(null)}>Cancel</Button>
                <Button className={PHONE_ACTION} disabled={answering} onClick={() => answer(confirm)}>
                  <Spinner on={answering} />{confirm ? "Yes, accept" : "Yes, decline"}
                </Button>
              </div>
            </div>
          )}
          {!writes && <p className="text-xs text-muted-foreground">Test mode: the answer goes to this server&apos;s fake, not ESPN.</p>}
        </div>
      )}
    </>
  );
  const onOpenChange = (o: boolean) => {
    setOpen(o);
    if (o) {
      const g = offer?.give ?? [], r = offer?.get ?? [];
      setGive(g);
      setGet(r);
      load(g, r);
    } else {
      last.current++; // an answer still on its way is for a sheet that has closed
    }
  };
  return (
    <>
      <Button variant="outline" size="sm" className={`${LABEL} ${className} text-foreground max-sm:h-10 max-sm:px-3`} onClick={() => onOpenChange(true)}>
        <ArrowLeftRight aria-hidden />{offer?.label ?? "Trade"}
      </Button>
      {desk ? (
        <Dialog open={open} onOpenChange={onOpenChange}>
          <DialogContent ref={panel} initialFocus={panel} className="flex h-[min(calc(var(--vv-h,100dvh)-4rem),46rem)] flex-col gap-3 rounded-lg p-4 shadow-[0_24px_48px_-20px_rgb(0_0_0_/_0.6)] sm:max-w-md">
            {body}
          </DialogContent>
        </Dialog>
      ) : (
        <Sheet open={open} onOpenChange={onOpenChange}>
          {/* a fixed height, so a tab or a price arriving never moves the sheet under the thumb */}
          <SheetContent ref={panel} initialFocus={panel} side="bottom" className={`data-[side=bottom]:h-[88dvh] gap-3 rounded-t-xl px-4 pt-4 ${PHONE_INSET}`}>
            {body}
          </SheetContent>
        </Sheet>
      )}
    </>
  );
}
