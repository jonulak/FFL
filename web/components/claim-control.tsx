"use client";

import { Plus } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { addTarget, capacity, groupPos, removeFrom, rowTag } from "@/lib/claims";
import { useClaimsMaybe } from "./claims-context";
import { Swap } from "./motion";

const FACE = "sm:min-w-[4.25rem] font-display text-[0.8125rem] font-semibold uppercase tracking-[0.08em] tabular-nums";

// A free agent's place in the week's claims, on his row: "+ claim" when he is in no group, his
// position and rank in his group ("WR·2") when he is. The menu puts him in a group (moving him
// out of any other), starts a new group with him, or takes him out. Nothing without the plan.
// `className` places it per width: the table's own column when the table is wide enough, the
// row's action line under the name when it is not.
// The menu mounts on the first tap: the Players table holds two of these per free agent (one per
// width) on 150 rows, and 300 menu roots were most of the page's hydration. Until then the trigger
// is the same button, drawn bare.
export function ClaimControl({ id, name, className = "" }: { id: number; name: string; className?: string }) {
  const c = useClaimsMaybe();
  const [armed, setArmed] = useState(false);
  const [open, setOpen] = useState(false);
  if (!c) return null;
  const { groups, pos, setGroups, submitting } = c;
  const tag = rowTag(id, groups, pos);
  const at = groups.findIndex((g) => g.targets.includes(id));
  const label = tag ? `${name}: ${tag} in claim group ${at + 1}, change` : `Add ${name} to a claim group`;
  const face = <Swap k={tag ?? "+"} className="inline-flex items-center gap-1">{tag ?? <><Plus aria-hidden className="stroke-[2.5]" />claim</>}</Swap>;
  if (!armed) {
    return (
      <span className={`inline-flex items-center gap-x-1 ${className}`}>
        <Button size="xs" variant={tag ? "default" : "outline"} aria-label={label} aria-haspopup="menu" aria-expanded={false} disabled={submitting}
          onClick={() => { setArmed(true); setOpen(true); }} className={FACE}>
          {face}
        </Button>
      </span>
    );
  }
  return (
    <span className={`inline-flex items-center gap-x-1 ${className}`}>
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger disabled={submitting}
        render={<Button size="xs" variant={tag ? "default" : "outline"} aria-label={label} className={FACE} />}>
        {face}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-auto min-w-52">
        {groups.length > 0 && (
          <DropdownMenuGroup>
            <DropdownMenuLabel className="font-display text-[0.6875rem] font-semibold uppercase tracking-[0.12em]">Add to group</DropdownMenuLabel>
            {groups.map((g, i) => (
              <DropdownMenuItem key={i} disabled={i === at} onClick={() => setGroups(addTarget(groups, i, id))}>
                <span className="font-display font-semibold tabular-nums">{i + 1} · {groupPos(g, pos) || "no targets"}</span>
                <span className="ml-auto pl-3 text-xs tabular-nums text-muted-foreground">get {capacity(g)} of {g.targets.length}</span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
        )}
        {groups.length > 0 && <DropdownMenuSeparator />}
        <DropdownMenuItem onClick={() => setGroups(addTarget(groups, "new", id))}>New group</DropdownMenuItem>
        {tag && <DropdownMenuItem variant="destructive" onClick={() => setGroups(removeFrom(groups, at, "targets", id))}>Remove from claims</DropdownMenuItem>}
      </DropdownMenuContent>
    </DropdownMenu>
    </span>
  );
}
