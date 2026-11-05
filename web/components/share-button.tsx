"use client";

import { Share } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { leagueOf, withLeague } from "@/lib/league";
import { type ShareKind, shareFile, shareHref } from "@/lib/share";
import { cn } from "cn";

const IMAGE_MS = 20_000;

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

// The page's image (app/share/route.tsx) to the share sheet, or saved where a browser cannot share a file.
export function ShareButton({ kind, week, team = null, className }: { kind: ShareKind; week: number; team?: number | null; className?: string }) {
  const [busy, setBusy] = useState(false);
  const share = async () => {
    setBusy(true);
    try {
      const r = await fetch(withLeague(shareHref({ kind, week, team }), leagueOf(window.location.search)), { signal: AbortSignal.timeout(IMAGE_MS) });
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
    <Button variant="ghost" size="sm" disabled={busy} onClick={() => void share()}
      className={cn("gap-1.5 px-1.5 font-display text-[0.9375rem] font-semibold uppercase tracking-[0.12em] text-foreground/55 max-sm:h-12 max-sm:-my-2 hover:text-foreground/85", className)}>
      {busy ? <Spinner /> : <Share aria-hidden />}
      Share
    </Button>
  );
}
