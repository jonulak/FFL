"use client";

import { createContext, useContext, useState } from "react";
import { useRouter } from "@/components/league-link";
import { clientPost } from "@/app/api-client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import type { DecisionPrefs } from "@/lib/api";
import { DEFAULT_DECISION_PREFS } from "@/lib/decisions";
import { Swap } from "./motion";

// The Settings switches, read once by the layout and handed to every page: the nav's Decisions tab, the player
// dialog's Decisions section and the moves' "Why?" prompts all follow them. A save refreshes the layout.
const Ctx = createContext<DecisionPrefs>(DEFAULT_DECISION_PREFS);

export function DecisionPrefsProvider({ prefs, children }: { prefs: DecisionPrefs; children: React.ReactNode }) {
  return <Ctx.Provider value={prefs}>{children}</Ctx.Provider>;
}

export const useDecisionPrefs = () => useContext(Ctx);

const SWITCHES = [["log", "Log decisions", "Record lineup moves, sits and claims, and ask why after each"],
                  ["page", "Decisions page", "The tab, the page, and the decisions in a player's card"]] as const;

export function DecisionSettings() {
  const router = useRouter();
  const [prefs, setPrefs] = useState(useDecisionPrefs());
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function save(p: Partial<DecisionPrefs>) {
    setBusy(true); setMsg(null);
    try { setPrefs(await clientPost<DecisionPrefs>("/decisions/prefs", p)); router.refresh(); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  return (
    <Card>
      <CardHeader><CardTitle>Decisions</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        {SWITCHES.map(([k, label, hint]) => {
          // no page, no logging (the server holds `log` off with it): the switch shows it and stays put
          const held = k === "log" && !prefs.page;
          return (
            <Label key={k} className="flex items-center justify-between gap-4">
              <span>
                <span className="font-medium">{label}</span>
                <span className="block text-xs text-muted-foreground"><Swap>{held ? "Off while the Decisions page is off" : hint}</Swap></span>
              </span>
              <Switch checked={prefs[k]} onCheckedChange={(v) => save({ [k]: v })} disabled={busy || held} />
            </Label>
          );
        })}
        {msg && <p key={msg} role="status" className="log-in text-sm">{msg}</p>}
      </CardContent>
    </Card>
  );
}
