---
version: 1
slug: "web-app-waivers"
primary_target: "web/app/waivers"
related_targets: ["web/components/waiver-table.tsx","web/components/pos-tabs.tsx"]
---

# Waivers page

Scope: `/waivers` (web/app/waivers/page.tsx, web/components/waiver-table.tsx, pos-tabs.tsx). Mode: Operate. Brought onto the board 2026-09-09.

Audience and job: the one manager, mostly on a phone in the Tuesday/Wednesday waiver window and at a laptop beside ESPN, asking "who is worth a claim at this position and why". Action: submit the claim in ESPN. Proof: `add`, then this week's projection, ROS and the two deltas beside it, the injury status with the return week, the matchup. Constraints: rows arrive ranked by `add` from `season.waiver_deltas` and are never re-sorted; the UI adds no judgment.

Chosen direction: the broadcast board, inherited from Roster, Matchup and League. Memorable moment: the free-agent pool as one full-width board of team-colour tiles, the START chip on anyone who would go straight into this week's lineup.

## Direction contract

THESIS: The pool as a single board table, decision column bold, everything else stepped back. Refuses the stock shadcn table with pill badges.

OWN-WORLD: Same field, panel, condensed numerals and Geist captions as the League table; the Matchup page's row (team-colour tile, name, POS · TEAM · vs/at · logo · OPP · kickoff) with the waiver numbers beside it. Lime START with the lineup gain; amber/red injury chips; the muted BYE chip.

STORY: He picks the position from the chyron strip, reads down `add` for anyone above zero, checks the chip stack and the matchup, then reads ROS and the deltas to see why, and goes to ESPN to claim.

FIRST VIEWPORT: Title "Waivers Week n" + stale badge; the definition caption (laptop only); the position tabs on a hairline; the board card with the header row and the first five or six players. On the phone the ROS and delta columns fold into a third caption line under the name so nothing is hidden.

FORM: Extension of the board; user-specified ("extend the new design to the waiver page, shadcn components whenever possible"): Tabs (line variant, rendered as Links), Card, Table, Badge.

FINISH: reviewed in two screenshot rounds (phone/laptop, light/dark, RB / D/ST / K), console clean apart from one ESPN CDN 404 the image fallback absorbs, detector clean, documented in DESIGN.md.

2026-09-21, recent form: the seven "last 3 games" columns joined the row as a second stat block beside REST OF SEASON (block headings over hairlines, a left hairline at each block's first column), one numeral per column, only the columns the position has. A single "form" column holding all seven as a vertical strip was shipped first and rejected ("a million stats in one column"). On the phone the projection and form figures are two strips on a second row per player. Three screenshot rounds (WR dark, K light; phone and laptop), rows measured at 79px laptop / 95px phone identity row, detector clean.
