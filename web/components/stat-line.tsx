// A player's box-score line (ffl/live.py::stat_line), set where the reason line would be: Geist
// 13px at 85% ink, the play feed's register for the one fact in a row. The API joins groups with
// " · " (passing · rushing · receiving); each group stays on one line and a break can only fall
// between groups, so "5 car, 29 yds" never splits from its own numbers on a phone. The dot
// travels with the group it introduces, so a wrapped line starts "· 5 car" rather than ending
// on a dangling separator.
export function StatLine({ text, className = "" }: { text: string; className?: string }) {
  const groups = text.split(" · ");
  return (
    <p className={`text-[13px] leading-snug tabular-nums text-foreground/85 ${className}`}>
      {groups.map((g, i) => (
        <span key={i}>
          {i > 0 && " "}
          <span className="whitespace-nowrap">{i > 0 && <span aria-hidden>· </span>}{g}</span>
        </span>
      ))}
    </p>
  );
}
