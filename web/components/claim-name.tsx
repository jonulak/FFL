"use client";

import { NAME_LINK } from "@/lib/player-log";
import { useClaims } from "./claims-context";
import { PlayerTrigger } from "./player-dialog";

// A player's name in the claims panel or the Submit dialog: tap it for his season modal, as on the
// table's rows. A name the payload no longer knows stays plain text.
export function ClaimName({ id, className = "" }: { id: number; className?: string }) {
  const { player } = useClaims();
  const p = player(id);
  if (!p) return <span className={className}>player {id}</span>;
  return (
    <PlayerTrigger p={{ player_id: id, name: p.name, pos: p.pos, team: p.team }} className={`max-w-full truncate rounded-sm align-bottom ${NAME_LINK} ${className}`}>
      {p.name}
    </PlayerTrigger>
  );
}
