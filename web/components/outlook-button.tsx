"use client";

import { useRouter } from "@/components/league-link";
import { useState } from "react";
import { toast } from "sonner";
import { clientPost } from "@/app/api-client";
import { Button } from "@/components/ui/button";
import type { OutlookStarted } from "@/lib/api";
import { Swap } from "./motion";

// Starts the page-wide outlook run. The answer is a toast: the button itself says Writing while it runs.
export function OutlookButton({ running }: { running: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function go() {
    setBusy(true);
    try {
      // named, home as an empty league (api-client puts another league in its place): a bare call is launchd's, every league
      await clientPost<OutlookStarted>("/outlook/refresh?league=");
      toast("writing — reload in a few minutes");
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button size="sm" variant="outline" onClick={go} disabled={busy || running}>
      <Swap>{running ? "Writing…" : busy ? "Starting…" : "Regenerate outlooks"}</Swap>
    </Button>
  );
}
