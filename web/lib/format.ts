export function pts(n: number | null | undefined): string {
  return n == null ? "—" : n.toFixed(1);
}

export function signed(n: number): string {
  return n >= 0 ? `+${n.toFixed(1)}` : `−${Math.abs(n).toFixed(1)}`;
}

export function injuryTone(status: string): "ok" | "warn" | "bad" {
  if (status === "QUESTIONABLE" || status === "DOUBTFUL") return "warn";
  if (status === "OUT" || status === "INJURY_RESERVE" || status === "SUSPENSION") return "bad";
  return "ok";
}

// ESPN's injury status, plus the week the player is expected back while that is still ahead of us.
export function injuryLabel(status: string, returnWeek: number | null, week: number): string {
  return returnWeek != null && returnWeek > week ? `${status} · wk ${returnWeek}` : status;
}
