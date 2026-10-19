"use client";

import { Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import { clientGet } from "@/app/api-client";
import { Combobox, ComboboxContent, ComboboxEmpty, ComboboxInput, ComboboxItem, ComboboxList } from "@/components/ui/combobox";
import { InputGroupAddon } from "@/components/ui/input-group";
import { Spinner as Spin } from "@/components/ui/spinner";
import type { NewsHit } from "@/lib/api";
import { playerNewsHref } from "@/lib/news";

const DEBOUNCE_MS = 250;

// Any NFL player by name (GET /news/search: the news desk's player index, most widely held first), picked to his
// news page. The list is the server's answer, so the combobox does no filtering of its own.
export function NewsSearch() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<NewsHit[]>([]);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [going, start] = useTransition();

  useEffect(() => {
    const term = q.trim();
    if (!term) return;
    let live = true;
    const t = setTimeout(() => {
      setBusy(true);
      clientGet<{ players: NewsHit[] }>(`/news/search?q=${encodeURIComponent(term)}`)
        .then((d) => { if (live) { setHits(d.players); setFailed(false); } })
        .catch(() => { if (live) { setHits([]); setFailed(true); } })
        .finally(() => { if (live) setBusy(false); });
    }, DEBOUNCE_MS);
    return () => { live = false; clearTimeout(t); };
  }, [q]);

  const shown = q.trim() ? hits : [];
  return (
    <Combobox<NewsHit> items={shown} filter={null} value={null}
      inputValue={q} onInputValueChange={(v) => setQ(v)}
      itemToStringLabel={(h) => h.name} isItemEqualToValue={(a, b) => a.player_id === b.player_id}
      onValueChange={(h) => { if (h) start(() => router.push(playerNewsHref(h.player_id))); }}>
      <ComboboxInput placeholder="Search a player" aria-label="Search a player's news by name" showTrigger={false}
        className="h-10 w-full rounded-md border-foreground/15 bg-card sm:h-8 sm:w-72 dark:bg-card">
        <InputGroupAddon>
          {/* the glass turns into the spinner while a search or the page is in flight (waiver-search's crossfade) */}
          <span aria-hidden className="grid *:col-start-1 *:row-start-1 *:transition-[opacity,scale] *:duration-200 *:ease-(--ease-arrive)">
            <Search className={`size-4 ${busy || going ? "scale-75 opacity-0" : ""}`} />
            <Spin aria-hidden role="presentation" className={`size-4 ${busy || going ? "" : "scale-75 opacity-0 animate-none"}`} />
          </span>
        </InputGroupAddon>
      </ComboboxInput>
      <ComboboxContent>
        <ComboboxEmpty>{!q.trim() ? "Type a name." : busy ? "Searching…" : failed ? "The player list didn't load. Try again." : "Nobody by that name."}</ComboboxEmpty>
        <ComboboxList>
          {(h: NewsHit) => (
            <ComboboxItem key={h.player_id} value={h}>
              <span className="min-w-0 flex-1 font-display text-[0.9375rem] font-semibold leading-tight tracking-tight">{h.name}</span>
              <span className="text-xs tabular-nums text-muted-foreground">{[h.pos, h.team].filter(Boolean).join(" · ")}</span>
            </ComboboxItem>
          )}
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  );
}
