"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { clientPost } from "@/app/api-client";
import { Button } from "@/components/ui/button";
import type { OutlookStarted } from "@/lib/api";
import { useOpenLeague } from "./league-context";
import { Swap } from "./motion";

// Starts the page-wide outlook run. The answer is a toast: the button itself says Writing while it runs.
export function OutlookButton({ running }: { running: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const { home } = useOpenLeague();

  async function go() {
    setBusy(true);
    try {
      await clientPost<OutlookStarted>("/outlook/refresh");
      toast("writing — reload in a few minutes");
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!home) return null; // outlooks are home's alone until 4.7
  return (
    <Button size="sm" variant="outline" onClick={go} disabled={busy || running}>
      <Swap>{running ? "Writing…" : busy ? "Starting…" : "Regenerate outlooks"}</Swap>
    </Button>
  );
}
