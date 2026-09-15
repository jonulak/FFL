// A player's box-score line (ffl/live.py::stat_line), set where the reason line would be: Geist
// 13px at 85% ink, the play feed's register for the one fact in a row. The API joins groups with
// " · " (passing · rushing · receiving); each group stays on one line and a break can only fall
// between groups, so "5 car, 29 yds" never splits from its own numbers on a phone. The dot
// travels with the group it introduces, so a wrapped line starts "· 5 car" rather than ending
// on a dangling separator.
// `wrap`: a group may break inside as well. For the Matchup board's half-width columns, where a
// D/ST group ("2 sk, 1 INT, 1 FR, 23 PA, 251 YA") is wider than the column and would otherwise
// run across the spine into the other side's row.
export function StatLine({ text, className = "", wrap = false }: { text: string; className?: string; wrap?: boolean }) {
  const groups = text.split(" · ");
  return (
    <p className={`text-[13px] leading-snug tabular-nums text-foreground/85 ${className}`}>
      {groups.map((g, i) => (
        <span key={i}>
          {i > 0 && " "}
          <span className={wrap ? "" : "whitespace-nowrap"}>{i > 0 && <span aria-hidden>· </span>}{g}</span>
        </span>
      ))}
    </p>
  );
}
