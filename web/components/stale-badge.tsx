"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Presence } from "./motion";

// Fades in when the data goes stale and out when a refresh brings it back; while it leaves it
// still says when it went stale.
export function StaleBadge({ since }: { since: string | null }) {
  const [last, setLast] = useState(since);
  if (since && since !== last) setLast(since);
  return <Presence show={since != null}><Badge variant="destructive">stale since {since ?? last}</Badge></Presence>;
}
