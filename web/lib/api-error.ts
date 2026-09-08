type Validation = { loc?: unknown[]; msg?: string };

// FastAPI reports a rejected body as `detail`: a list of {loc, msg} for a 422 validation
// error, an object (`{source, error}`) from our own handlers, or a bare string. Flatten all
// three into one readable line — the raw object renders as "[object Object]".
export function apiErrorMessage(status: number, statusText: string, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  const say = (s: string) => `${status} ${statusText}: ${s}`;
  if (Array.isArray(detail)) {
    const lines = (detail as Validation[])
      .map((d) => {
        const field = Array.isArray(d.loc) ? d.loc.filter((p) => p !== "body").join(".") : "";
        const msg = d.msg ?? "invalid";
        return field ? `${field}: ${msg}` : msg;
      })
      .filter(Boolean);
    if (lines.length) return say(lines.join("; "));
  }
  if (typeof detail === "string" && detail) return say(detail);
  if (detail && typeof detail === "object") {
    const o = detail as { error?: string; source?: string };
    if (o.error) return say(o.source ? `${o.source}: ${o.error}` : o.error);
  }
  return `${status} ${statusText}`;
}
