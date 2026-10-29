"use client";

import { CircleAlert } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import type { LeagueProblem } from "@/lib/espn";
import { Hint } from "./hint";

// Why a listed league can't be picked (the wizard's Leagues step, Settings › ESPN connection): an alert mark whose
// hover says the problem in a line (Hint: never on touch) and whose tap or click opens what is wrong and how to fix it.
export function LeagueProblemMark({ problem, league }: { problem: LeagueProblem; league: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Hint text={problem.message}>
        <Button variant="ghost" size="icon" onClick={() => setOpen(true)} aria-label={`Why ${league} can't be picked`}
                className="size-11 shrink-0">
          <CircleAlert className="size-5" aria-hidden />
        </Button>
      </Hint>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <CircleAlert className="size-5 shrink-0" aria-hidden />{problem.title}
            </DialogTitle>
            <DialogDescription>{league}</DialogDescription>
          </DialogHeader>
          <p className="text-[15px] font-medium leading-snug">{problem.message}</p>
          <p className="text-sm leading-relaxed text-muted-foreground">{problem.fix}</p>
          <DialogFooter>
            <Button onClick={() => setOpen(false)} className="max-lg:h-12 max-lg:px-5 max-lg:text-base">OK</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
