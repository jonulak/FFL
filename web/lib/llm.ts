import type { LlmToken } from "./api";
import { errorCode } from "./auth";

// The wizard's first step: Claude for an admin whose server has no token (the status is only fetched for an admin), then
// Connect, or Leagues when a working ESPN login is already stored.
export function wizardStart(claude: LlmToken | null, espnReady: boolean): "claude" | "connect" | "leagues" {
  if (claude && !claude.set) return "claude";
  return espnReady ? "leagues" : "connect";
}

// Why POST /llm/token refused: the ping's own reason (502 claude_failed), a blank paste, or the plain status line.
export function tokenError(body: unknown, fallback: string): string {
  const code = errorCode(body);
  if (code === "claude_failed") {
    const msg = (body as { message?: unknown }).message;
    return `Claude didn't accept that token${typeof msg === "string" && msg ? `: ${msg}` : "."}`;
  }
  if (code === "no_token") return "Paste a token first.";
  return `Not saved: ${fallback}`;
}
