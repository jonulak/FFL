"use client";

import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { addTarget, capacity, groupPos, removeFrom, rowTag } from "@/lib/claims";
import { useClaimsMaybe } from "./claims-context";

// A free agent's place in the week's claims, on his row: "+ claim" when he is in no group, his
// position and rank in his group ("WR·2") when he is. The menu puts him in a group (moving him
// out of any other), starts a new group with him, or takes him out. Nothing without the plan.
// `className` places it per breakpoint: the table's own column from `sm`, the row's caption line
// on a phone (where the column would sit off-screen, past the sideways scroll); `sep` leads it
// with the caption's dot.
export function ClaimControl({ id, name, className = "", sep = false }: { id: number; name: string; className?: string; sep?: boolean }) {
  const c = useClaimsMaybe();
  if (!c) return null;
  const { groups, pos, setGroups, submitting } = c;
  const tag = rowTag(id, groups, pos);
  const at = groups.findIndex((g) => g.targets.includes(id));
  return (
    <span className={`inline-flex items-center gap-x-1 ${className}`}>
    {sep && <span aria-hidden>·</span>}
    <DropdownMenu>
      <DropdownMenuTrigger disabled={submitting}
        render={
          <Button size="xs" variant={tag ? "default" : "outline"} aria-label={tag ? `${name}: ${tag} in claim group ${at + 1}, change` : `Add ${name} to a claim group`}
            className="sm:min-w-[4.25rem] font-display text-[0.8125rem] font-semibold uppercase tracking-[0.08em] tabular-nums" />
        }>
        {tag ?? <><Plus aria-hidden className="stroke-[2.5]" />claim</>}
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
