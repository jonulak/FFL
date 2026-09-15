---
version: 1
slug: "web-app-page-tsx"
primary_target: "web/app/page.tsx"
related_targets: ["web/components/matchup.tsx"]
---

# Matchup page

Scope: `/` (web/app/page.tsx, web/components/matchup.tsx, matchup-live.tsx). Mode: Operate. Renamed from "Week" on 2026-09-08.

Audience and job: the one manager, on a phone through game day and at a laptop midweek, asking "where does this week stand" for the head-to-head: both totals, both lineups, who is questionable, and what the lineup by projection would add. Action: set the lineup in ESPN (the call itself lives on the Roster page). Proof: projection per player and per side, posted points once games are on, the optimal-lineup gain. Data can be stale; the stale badge stays. The page polls every minute while the week is live.

Chosen direction: the broadcast board, inherited from the Roster page. Memorable moment: the scorebug, both club marks and their totals set like the corner score of a broadcast, over one lineup board where the two sides face each other slot for slot across a spine of slot codes. (Revised 2026-09-14 from two side-by-side cards: "both rosters viewed side by side" meant slot-aligned, at every width including the phone.)

## Direction contract

THESIS: A head-to-head read like a broadcast scorebug over one slot-aligned lineup board. Refuses the shadcn table-in-a-card with a "proj · actual" caption, and refuses two independent lists that only sit next to each other.

OWN-WORLD: Same board as Roster: field, panel, condensed numerals, Geist for prose, verdict chips for injury only (no START/BENCH here; that is the Roster page's job). Team-colour tiles at row scale reuse `TeamPanel`; each half of a row is a shadcn `Item`, the club marks are the shadcn `Avatar`-based `ClubLogo`.

STORY: He sees the two marks, the two totals and which side leads, reads the board slot by slot (his QB against theirs, his FLEX against theirs), scans for questionable tags, checks the by-projection footer for a swap, and glances at the latest plays for what just moved.

FIRST VIEWPORT: Title "Matchup" + week + live chip + stale badge. Scorebug: my mark and team left, opponent's right, big total with PROJ/PTS unit, "by projection" note when the optimal lineup differs. Latest plays as one-line rows once the week has a play, two of them on the phone so the first two starters still land on the first screen; from `sm` the two totals meet at VS. Then the board: MY LINEUP · state at the left, state · opponent at the right; each line is my half (tile, name + injury chip, pos · team · vs/at logo opponent, kickoff, number), the slot code, and the opponent's half mirrored; totals ruled off per side, bench and IR under labels at each edge, the by-projection swaps per side as the footer. From `sm` the pair sits either side of the spine; on the phone each slot stacks my line over the opponent's at full width (his tile at the right edge), the totals stack with the side named at the left. The half-width phone columns tried first read as crowded and hard to parse.

FORM: Extension of the Roster page's world; user-specified ("extend the design elements of the roster page"), no seed.

FINISH: reviewed in two screenshot rounds (phone/laptop, light/dark), console clean, documented in DESIGN.md.
