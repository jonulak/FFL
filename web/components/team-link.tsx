import Link from "next/link";
import { NAME_LINK } from "@/lib/player-log";
import { teamHref } from "@/lib/team-link";

// A fantasy team's name that opens its roster list, set like a player name that opens the
// season modal: plain type at rest, underlined under a pointer. An id with no team behind it
// leaves the name as plain text. No "use client": server components render it too.
// `linked` false: plain text (another league, whose team pages are not open yet).
export function TeamLink({ id, className = "", linked = true, children }: { id: number | string | null | undefined; className?: string; linked?: boolean; children: React.ReactNode }) {
  const href = linked ? teamHref(id) : null;
  if (!href) return <span className={className}>{children}</span>;
  return (
    <Link href={href} className={`rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/60 ${NAME_LINK} ${className}`}>
      {children}
    </Link>
  );
}
