import type { AppSettings, LlmToken } from "./api";
import { errorCode } from "./auth";

// The wizard's first step: Claude for an admin whose server has no token, then Services while none of its optional
// settings is saved (both statuses are fetched for an admin only), then Connect, or Leagues when a working ESPN login
// is already stored.
export function wizardStart(claude: LlmToken | null, services: AppSettings | null, espnReady: boolean):
    "claude" | "services" | "connect" | "leagues" {
  if (claude && !claude.set) return "claude";
  if (servicesDue(services)) return "services";
  return espnReady ? "leagues" : "connect";
}

// The Services step shows on a first run until any of its settings is saved; Settings › App holds them after.
export function servicesDue(services: AppSettings | null): boolean {
  return services !== null && !services.youtube_key_set && !services.push_contact;
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
