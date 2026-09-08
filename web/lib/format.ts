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
