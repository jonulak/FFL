import Link from "next/link";

const LINKS = [["/", "Week"], ["/league", "League"], ["/waivers", "Waivers"], ["/journal", "Journal"], ["/draft", "Draft"]] as const;

export function Nav({ right }: { right?: React.ReactNode }) {
  return (
    <header className="sticky top-0 z-10 border-b bg-background/95 backdrop-blur">
      <nav className="mx-auto flex max-w-5xl items-center gap-1 overflow-x-auto px-3 py-2 text-sm">
        {LINKS.map(([href, label]) => (
          <Link key={href} href={href} className="rounded px-3 py-1.5 hover:bg-muted whitespace-nowrap">{label}</Link>
        ))}
        <div className="ml-auto">{right ?? <div id="refresh-slot" />}</div>
      </nav>
    </header>
  );
}
