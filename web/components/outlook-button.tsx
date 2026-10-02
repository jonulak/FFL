"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { clientPost } from "@/app/api-client";
import { Button } from "@/components/ui/button";
import type { OutlookStarted } from "@/lib/api";

export function OutlookButton({ running }: { running: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function go() {
    setBusy(true);
    setMsg(null);
    try {
      await clientPost<OutlookStarted>("/outlook/refresh");
      setMsg("writing — reload in a few minutes");
      router.refresh();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      {msg && <span className="max-w-60 truncate text-xs text-muted-foreground">{msg}</span>}
      <Button size="sm" variant="outline" onClick={go} disabled={busy || running}>
        {running ? "Writing…" : busy ? "Starting…" : "Regenerate outlooks"}
      </Button>
    </div>
  );
}
