"use client";

import { Share } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { leagueOf, withLeague } from "@/lib/league";
import { type ShareKind, shareFile, shareHref } from "@/lib/share";
import { cn } from "cn";

const IMAGE_MS = 20_000;
const TEAMS_KEY = "ffl-share-teams";

function save(file: File) {
  const url = URL.createObjectURL(file);
  const a = document.createElement("a");
  a.href = url;
  a.download = file.name;
  a.click();
  URL.revokeObjectURL(url);
}

async function deliver(file: File) {
  const outcome = await shareFile(file, navigator);
  if (outcome === "unsupported") save(file);
  // the tap went stale while the image rendered: only a new one can open the sheet
  if (outcome === "blocked") toast("Image ready", { duration: 10_000, action: { label: "Share", onClick: () => void deliver(file).catch(failed) } });
}

function failed() {
  toast.error("Couldn't make the image. Try again.");
}

// The page's image (app/share/route.tsx) to the share sheet, or saved where a browser cannot share a file. The tap opens
// a menu for the one choice, so the tap on Share image is the fresh one the sheet needs.
export function ShareButton({ kind, week, team = null, className }: { kind: ShareKind; week: number; team?: number | null; className?: string }) {
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const [teams, setTeams] = useState(true);
  const openChange = (next: boolean) => {
    if (next) setTeams(localStorage.getItem(TEAMS_KEY) !== "0"); // here, not in useState: the server has no localStorage
    setOpen(next);
  };
  const chooseTeams = (next: boolean) => {
    setTeams(next);
    localStorage.setItem(TEAMS_KEY, next ? "1" : "0");
  };
  const share = async () => {
    setOpen(false);
    setBusy(true);
    try {
      const r = await fetch(withLeague(shareHref({ kind, week, team, teams }), leagueOf(window.location.search)), { signal: AbortSignal.timeout(IMAGE_MS) });
      // a lapsed session lands on the sign-in page: an answer, but not an image
      if (!r.ok || r.headers.get("content-type") !== "image/png") throw new Error("no image");
      await deliver(new File([await r.blob()], `ffl-${kind}-week-${week}.png`, { type: "image/png" }));
    } catch {
      failed();
    } finally {
      setBusy(false);
    }
  };
  return (
    <Popover open={open} onOpenChange={openChange}>
      <PopoverTrigger disabled={busy} render={
        <Button variant="ghost" size="sm"
          className={cn("gap-1.5 px-1.5 font-display text-[0.9375rem] font-semibold uppercase tracking-[0.12em] text-foreground/55 max-sm:h-12 max-sm:-my-2 hover:text-foreground/85", className)} />
      }>
        {busy ? <Spinner /> : <Share aria-hidden />}
        Share
      </PopoverTrigger>
      <PopoverContent align="end" className="w-64 gap-1 p-3">
        <label className="flex min-h-12 cursor-pointer items-center justify-between gap-3 font-medium sm:min-h-9">
          Team name and logo
          <Switch className="data-unchecked:bg-foreground/20 dark:data-unchecked:bg-foreground/25" checked={teams} onCheckedChange={chooseTeams} />
        </label>
        <Button className="w-full max-sm:h-12" onClick={() => void share()}>Share image</Button>
      </PopoverContent>
    </Popover>
  );
}
