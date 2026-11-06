"use client";

import { Bell, Check, Trash2 } from "lucide-react";
import { useRouter as useNextRouter, usePathname, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { clientGet, clientPost } from "@/app/api-client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Item, ItemActions, ItemGroup } from "@/components/ui/item";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import type { NotificationItem, Notifications } from "@/lib/api";
import { generation } from "@/lib/generation";
import { leagueHref } from "@/lib/league";
import { countLabel, opensOtherLeague, withoutSeen } from "@/lib/notify";
import { Hint } from "./hint";
import { Ago } from "./local-time";
import { Presence, Ticker, useReorder } from "./motion";

const REFRESH_MS = 5 * 60_000; // a window left open: the jobs that queue notices run every 10 to 15 minutes

type Unread = { unread: number };

const itemKey = (i: NotificationItem) => `${i.rows[0].league}|${i.rows[0].key}`;

// A tapped push opens its page with ?seen=<its tag> (ffl/notify.py for_league). Apart from the bell so useSearchParams,
// which needs a Suspense boundary, holds up nothing else. A failed read leaves it unread: nothing is lost.
function SeenFromPush({ onUnread }: { onUnread: (n: number) => void }) {
  const seen = useSearchParams().get("seen");
  useEffect(() => {
    if (!seen) return;
    const { pathname, search, hash } = window.location;
    window.history.replaceState(null, "", withoutSeen(pathname, search, hash));
    clientPost<Unread>("/notifications/seen", { tag: seen }).then((r) => onUnread(r.unread)).catch(() => {});
  }, [seen, onUnread]);
  return null;
}

// The bell beside the gear: every league's notifications, the unread count on the icon and on the Home Screen icon.
export function NotificationsBell({ unread: initial, openLeague }: { unread: number | null; openLeague: string | null }) {
  const router = useNextRouter(); // not league-link's: an item names the league it opens
  const path = usePathname();
  const [unread, setUnread] = useState(initial);
  const [items, setItems] = useState<NotificationItem[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  const writes = useRef(generation()); // a count read before a mark must not land over it
  const list = useReorder<HTMLDivElement>(items);

  const refresh = useCallback((limit: number) => {
    const ticket = writes.current.ticket();
    return clientGet<Notifications>(`/notifications?limit=${limit}`).then((r) => {
      if (!writes.current.fresh(ticket)) return;
      setUnread(r.unread);
      if (limit > 0) { setItems(r.items); setFailed(false); }
    });
  }, []);

  const landed = useCallback((n: number) => {
    writes.current.bump();
    setUnread(n);
  }, []);

  // Web apps get no badge permission of their own; without the notifications permission this is refused, and that is fine.
  useEffect(() => {
    if (unread !== null && "setAppBadge" in navigator) navigator.setAppBadge(unread).catch(() => {});
  }, [unread]);

  const first = useRef(true);
  useEffect(() => {
    if (first.current) first.current = false; // the layout's count is this page's
    else refresh(0).catch(() => {});
  }, [path, refresh]);

  useEffect(() => {
    const visible = () => { if (document.visibilityState === "visible") refresh(0).catch(() => {}); };
    const timer = setInterval(visible, REFRESH_MS);
    document.addEventListener("visibilitychange", visible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [refresh]);

  const openChange = (next: boolean) => {
    setOpen(next);
    if (next) refresh(50).catch(() => setFailed(true));
  };

  // Shown at once; a refused write says so and the list is read again.
  function write(call: Promise<Unread>) {
    writes.current.bump();
    return call.then((r) => landed(r.unread)).catch((e) => {
      toast(e instanceof Error ? e.message : String(e));
      return refresh(50).catch(() => setFailed(true));
    });
  }

  function read(item: NotificationItem) {
    if (item.read) return Promise.resolve();
    setItems((all) => all?.map((i) => (i === item ? { ...i, read: true } : i)) ?? all);
    setUnread((n) => (n === null ? n : Math.max(0, n - 1)));
    return write(clientPost<Unread>("/notifications/read", { rows: item.rows }));
  }

  function remove(item: NotificationItem) {
    setItems((all) => all?.filter((i) => i !== item) ?? all);
    if (!item.read) setUnread((n) => (n === null ? n : Math.max(0, n - 1)));
    write(clientPost<Unread>("/notifications/delete", { rows: item.rows }));
  }

  function readAll() {
    setItems((all) => all?.map((i) => ({ ...i, read: true })) ?? all);
    setUnread(0);
    write(clientPost<Unread>("/notifications/read", { all: true }));
  }

  function go(item: NotificationItem) {
    setOpen(false);
    const done = read(item);
    const url = leagueHref(item.url, openLeague); // a news item names no league: it stays in the one on screen
    // a full load would cancel the write, so it waits; a soft move does not
    if (opensOtherLeague(url, openLeague)) done.finally(() => window.location.assign(url));
    else router.push(url);
  }

  const count = unread ?? 0;
  return (
    <>
      <Suspense fallback={null}><SeenFromPush onUnread={landed} /></Suspense>
      <Popover open={open} onOpenChange={openChange}>
        <Hint text="Notifications">
          <PopoverTrigger render={
            <Button variant="ghost" size="icon-sm" aria-label={count ? `Notifications, ${count} unread` : "Notifications"}
                    className="relative text-foreground/55 data-popup-open:text-foreground max-lg:size-11" />
          }>
            <Bell />
            <Presence show={count > 0}>
              <Badge aria-hidden className="absolute top-0 right-0 h-4 min-w-4 px-1 text-[0.6875rem] tabular-nums max-lg:top-1.5 max-lg:right-1.5">
                <Ticker value={count} format={(n) => countLabel(Math.round(n))} />
              </Badge>
            </Presence>
          </PopoverTrigger>
        </Hint>
        <PopoverContent align="end" className="w-[min(24rem,calc(100vw-1.5rem))] gap-0 p-0">
          <div className="flex min-h-12 items-center justify-between gap-3 border-b border-foreground/12 pr-1.5 pl-3 lg:min-h-10">
            <h2 className="font-display text-base font-semibold tracking-[0.14em] uppercase">Notifications</h2>
            {count > 0 && (
              <Button variant="ghost" size="sm" onClick={readAll} className="text-foreground/70 max-lg:h-11">Mark all read</Button>
            )}
          </div>
          <div ref={list} className="max-h-[min(28rem,70dvh)] overflow-y-auto overscroll-contain">
            {failed && !items && (
              <div className="flex items-center justify-between gap-3 p-3 text-sm">
                <span>Could not load your notifications.</span>
                <Button variant="outline" size="sm" className="max-lg:h-11" onClick={() => refresh(50).catch(() => setFailed(true))}>Try again</Button>
              </div>
            )}
            {!failed && !items && (
              <div className="space-y-3 p-3" aria-hidden>
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </div>
            )}
            {items?.length === 0 && <p className="p-3 text-sm text-muted-foreground">No notifications.</p>}
            {!!items?.length && (
              <ItemGroup className="gap-0 divide-y divide-foreground/10">
                {items.map((item) => (
                  <Item key={itemKey(item)} data-key={itemKey(item)} size="xs" className="flex-nowrap items-start gap-1 rounded-none py-1 pr-1 pl-3 lg:pr-1.5">
                    <button type="button" onClick={() => go(item)}
                            className="flex min-w-0 flex-1 gap-2 rounded-sm py-1.5 text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">
                      <span aria-hidden className={`mt-1.5 size-2 shrink-0 rounded-full ${item.read ? "" : "bg-foreground"}`} />
                      <span className="min-w-0 flex-1">
                        <span className={`block font-medium ${item.read ? "text-foreground/65" : ""}`}>
                          {!item.read && <span className="sr-only">Unread: </span>}{item.title}
                        </span>
                        <span className="line-clamp-2 text-muted-foreground">{item.body}</span>
                        <span className="mt-0.5 block text-xs text-muted-foreground">
                          {item.league && <>{item.league} · </>}
                          <Ago iso={item.created_at} />
                        </span>
                      </span>
                    </button>
                    <ItemActions className="shrink-0 gap-0">
                      {!item.read && (
                        <Hint text="Mark read">
                          <Button variant="ghost" size="icon-sm" aria-label={`Mark read: ${item.title}`} onClick={() => read(item)}
                                  className="text-foreground/55 max-lg:size-11"><Check /></Button>
                        </Hint>
                      )}
                      <Hint text="Delete">
                        <Button variant="ghost" size="icon-sm" aria-label={`Delete: ${item.title}`} onClick={() => remove(item)}
                                className="text-foreground/55 max-lg:size-11"><Trash2 /></Button>
                      </Hint>
                    </ItemActions>
                  </Item>
                ))}
              </ItemGroup>
            )}
          </div>
        </PopoverContent>
      </Popover>
    </>
  );
}
