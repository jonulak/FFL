"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { clientPost } from "@/app/api-client";
import { Button } from "@/components/ui/button";

// The one call where the user has asked to wait: the API syncs the journal and rebuilds every
// payload inline (about 12 s warm, up to 19 s cold), so the 20 s default abort is too close.
const REFRESH_TIMEOUT_MS = 30_000;

export function RefreshButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function go() {
    setBusy(true);
    setErr(null);
    try {
      await clientPost("/refresh", {}, REFRESH_TIMEOUT_MS);
      router.refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      {err && <span className="max-w-40 truncate text-xs text-destructive">{err}</span>}
      <Button size="sm" variant="outline" onClick={go} disabled={busy}>
        {busy ? "Refreshing…" : "Refresh"}
      </Button>
    </div>
  );
}
