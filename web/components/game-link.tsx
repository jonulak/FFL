import Link from "next/link";

// A game line made a way into that NFL game. The rows it sits in carry their own tap target (the Matchup
// breakdown is an overlay button, the Roster expand a chevron), so the link stands above it (relative z-10) and
// pads its tap target to about 44px tall without moving the line (-my/py). The text keeps its ink; a hairline
// underline comes on hover.
export function GameLink({ href, children, className = "" }: { href: string | null; children: React.ReactNode; className?: string }) {
  if (!href) return <>{children}</>;
  return (
    <Link href={href}
      className={`relative z-10 -my-2.5 inline-flex min-w-0 items-center py-2.5 decoration-foreground/30 underline-offset-2 outline-none hover:underline focus-visible:rounded-sm focus-visible:ring-2 focus-visible:ring-ring/60 ${className}`}>
      {children}
    </Link>
  );
}
