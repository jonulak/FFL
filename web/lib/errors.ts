import { ApiError } from "@/lib/api";

export type ErrorFields = { status: number; source: string; message: string };

// ErrorCard takes plain serialisable props; an Error object (or its `cause` chain) cannot
// cross the React Flight boundary. Flatten anything thrown by `apiGet` into those fields.
export function errorFields(e: unknown): ErrorFields {
  return e instanceof ApiError
    ? { status: e.status, source: e.source, message: e.message }
    : { status: 0, source: "app", message: e instanceof Error ? e.message : String(e) };
}
