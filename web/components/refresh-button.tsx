"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { clientPost } from "@/app/api-client";
import { Button } from "@/components/ui/button";

export function RefreshButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function go() {
    setBusy(true);
    setErr(null);
    try {
      await clientPost("/refresh");
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
