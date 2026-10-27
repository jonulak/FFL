import { unstable_rethrow } from "next/navigation";
import { ApiError } from "@/lib/api";

export type ErrorFields = { status: number; source: string; message: string };

// ErrorCard takes plain serialisable props; an Error object (or its `cause` chain) cannot
// cross the React Flight boundary. Flatten anything thrown by `apiGet` into those fields.
// Next's own control flow is not an error: a signed-out call's redirect to /signin (lib/api.ts) is thrown, and every
// page's `catch (e) { return <ErrorCard {...errorFields(e)} /> }` passes it on from here.
export function errorFields(e: unknown): ErrorFields {
  unstable_rethrow(e);
  return e instanceof ApiError
    ? { status: e.status, source: e.source, message: e.message }
    : { status: 0, source: "app", message: e instanceof Error ? e.message : String(e) };
}
