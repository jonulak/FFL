"use client";

import { useState } from "react";

// A player's box-score line (ffl/live.py::stat_line), set where the reason line would be: Geist
// 13px at 85% ink, the play feed's register for the one fact in a row. The API joins groups with
// " · " (passing · rushing · receiving); each group stays on one line and a break can only fall
// between groups, so "5 car, 29 yds" never splits from its own numbers on a phone. The dot
// travels with the group it introduces, so a wrapped line starts "· 5 car" rather than ending
// on a dangling separator.
// `wrap`: a group may break at its commas as well, each "5 car" / "29 yds" item still one piece.
// For the Matchup board's half-width columns, where a D/ST group ("2 sk, 1 INT, 1 FR, 23 PA,
// 251 YA") is wider than the column and would otherwise run across the spine into the other
// side's row; a number never parts from its unit either way.
// A new line (a stat that moved) crossfades in over the old; the first paint does not.
export function StatLine({ text, className = "", wrap = false, title }: { text: string; className?: string; wrap?: boolean; title?: string }) {
  const groups = text.split(" · ");
  const [first] = useState(text);
  return (
    <p key={text} className={`text-[0.8125rem] leading-snug tabular-nums text-foreground/85 ${text === first ? "" : "fx-swap"} ${className}`} title={title}>
      {groups.map((g, i) => (
        <span key={i}>
          {i > 0 && " "}
          {wrap ? (
            g.split(", ").map((item, j) => (
              <span key={j}>
                {j > 0 && ", "}
                <span className="whitespace-nowrap">{i > 0 && j === 0 && <span aria-hidden>· </span>}{item}</span>
              </span>
            ))
          ) : (
            <span className="whitespace-nowrap">{i > 0 && <span aria-hidden>· </span>}{g}</span>
          )}
        </span>
      ))}
    </p>
  );
}
