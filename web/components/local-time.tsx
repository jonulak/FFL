"use client";

import { useState } from "react";

// The API serves ISO timestamps in the Mac's zone; the phone renders them in its own. The server
// render and the client render can differ, so hydration warnings are suppressed on purpose.
export function Kickoff({ iso }: { iso: string | null }) {
  if (!iso) return <span>BYE</span>;
  const d = new Date(iso);
  return (
    <span suppressHydrationWarning>
      {d.toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}
    </span>
  );
}

export function Ago({ iso }: { iso: string }) {
  // The clock is impure, so read it once in a state initialiser rather than in the render body
  // (react-hooks/purity): the age is fixed for the life of the render, which is what a
  // server-rendered page wants anyway.
  const [now] = useState(() => Date.now());
  const mins = Math.max(0, Math.round((now - new Date(iso).getTime()) / 60_000));
  const text = mins < 60 ? `${mins}m ago` : mins < 48 * 60 ? `${Math.round(mins / 60)}h ago` : `${Math.round(mins / 1440)}d ago`;
  return <span suppressHydrationWarning>{text}</span>;
}
