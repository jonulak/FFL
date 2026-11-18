"use client";

import { useEffect, useState } from "react";
import { clientGet } from "@/app/api-client";
import { Item, ItemActions, ItemContent, ItemDescription, ItemTitle } from "@/components/ui/item";
import type { TradeOffer } from "@/lib/api";
import { LABEL } from "@/lib/board";
import { expiresLabel, offerLine } from "@/lib/trade";
import { TradeSheet } from "./trade-sheet";

// My pending trade offers off ESPN, sent and received, above the roster. Read after the page paints: the read is ESPN's
// pending document, a second or two, and the roster must not wait on it. Review opens the trade sheet on the offer's
// players. Answering stays on ESPN for now.
export function TradeOffers() {
  const [offers, setOffers] = useState<TradeOffer[]>([]);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    clientGet<{ offers: TradeOffer[] }>("/trades").then((d) => setOffers(d.offers), () => setFailed(true));
  }, []);
  if (failed) return <p className="text-xs text-muted-foreground">Couldn&apos;t read trade offers from ESPN.</p>;
  if (!offers.length) return null;
  return (
    <ul className="space-y-2">
      {offers.map((o) => (
        <li key={o.id}>
          <Item variant="outline" className="items-end gap-y-3">
            <ItemContent className="min-w-0 basis-56">
              <p className={LABEL}>{o.incoming ? `Offer from ${o.team.team}` : `Your offer to ${o.team.team}`}</p>
              <ItemTitle className="font-display text-[1.0625rem] font-semibold leading-tight tracking-tight text-pretty">{offerLine(o)}</ItemTitle>
              {o.expires && <ItemDescription className="text-xs tabular-nums">{expiresLabel(o.expires)}</ItemDescription>}
            </ItemContent>
            <ItemActions>
              <TradeSheet team={o.team.team_id} name={o.team.team} abbrev={o.team.abbrev} className=""
                offer={{ give: o.give.map((p) => p.player_id), get: o.get.map((p) => p.player_id), label: "Review" }} />
            </ItemActions>
          </Item>
        </li>
      ))}
    </ul>
  );
}
