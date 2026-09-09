---
version: 1
slug: "web-app-league"
primary_target: "web/app/league"
related_targets: ["web/components/league.tsx", "web/components/club-logo.tsx", "web/components/week-select.tsx"]
---

# League page

Scope: `/league` (web/app/league/page.tsx, web/components/league.tsx, club-logo.tsx, week-select.tsx). Mode: Operate. Brought onto the board 2026-09-09.

Audience and job: the one manager, mostly at a laptop midweek, asking "where does the league stand" (record, points for and against, the playoff cut) and "how did every matchup go" for a chosen week. The scoreboard defaults to the current week; a Select moves it to any regular-season week. Power rankings were dropped at the user's request.

Chosen direction: the broadcast board, inherited from Roster and Matchup. Memorable moment: the standings as one full-width board with every owner's mark, and the scoreboard as a grid of scorebugs, one per matchup.

## Direction contract

THESIS: Standings as one full-width board and the week's matchups as a grid of stacked scorebugs. Refuses the two-column dashboard of three small list cards.

OWN-WORLD: Same board as Roster and Matchup: field, panel, condensed numerals, Geist for captions, the muted ME chip the only chip. Fantasy-team marks (`ClubLogo`) at 32px in the table and 36px in the scorebug, with an abbreviation tile under them.

STORY: He reads his rank and record against the playoff line, then scans the week's five scorebugs for scores or projections, finds his own by the ring and the ME chip, and switches weeks from the Select to see a past result or a coming pairing.

FIRST VIEWPORT: Title "League" + stale badge. STANDINGS heading, the full-width table (rank, mark, name, W-L, PF, PA, streak; PA and streak hide on the phone). Then the SCOREBOARD heading with the count, the live chip and the week Select at its end, and the cards, two across from `md`.

FORM: Extension of the Roster page's world; user-specified ("extend the design to the league page"), shadcn primitives by default (Table, Card, Badge, Select), custom only for the image fallback.

FINISH: reviewed in two screenshot rounds (phone/laptop, light/dark, plus the week switch), console clean, detector clean, documented in DESIGN.md.
