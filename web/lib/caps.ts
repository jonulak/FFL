// Roster caps on the Settings page (ffl-soc.4.10, GET/POST /caps): per position the league's limit (read-only), my cap
// and the cap the draft tools use (the lower of the two). The same rows `ffl.leagues caps` writes.
export type CapRow = { pos: string; provider_limit: number | null; my_cap: number | null; cap: number | null };
export type Caps = { league: string; name: string; rows: CapRow[] };

// What a cap field's text means: a whole count, never above the league's limit (the API refuses it too).
export function parseCap(text: string, limit: number | null): { ok: number } | { error: string } {
  const t = text.trim();
  if (!/^\d+$/.test(t)) return { error: "A cap is a count of players." };
  const n = Number(t);
  if (limit != null && n > limit) return { error: `The league allows at most ${limit}.` };
  return { ok: n };
}
