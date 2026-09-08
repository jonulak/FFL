import Link from "next/link";

const POS = ["QB", "RB", "WR", "TE", "D/ST", "K"];

// Links, not a Tabs widget: the position is server state in `?pos=`, so each tab is a
// navigation the page re-renders from.
export function PosTabs({ active, base }: { active: string; base: string }) {
  return (
    <div className="flex gap-1 overflow-x-auto">
      {POS.map((p) => (
        <Link
          key={p}
          href={`${base}?pos=${encodeURIComponent(p)}`}
          aria-current={p === active ? "page" : undefined}
          className={`rounded px-3 py-1 text-sm whitespace-nowrap ${
            p === active ? "bg-primary text-primary-foreground" : "hover:bg-muted"
          }`}
        >
          {p}
        </Link>
      ))}
    </div>
  );
}
