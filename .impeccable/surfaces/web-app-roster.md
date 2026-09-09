---
version: 1
slug: "web-app-roster"
primary_target: "web/app/roster"
related_targets: ["web/components/roster-cards.tsx"]
---

# Roster page

Scope: `/roster` (web/app/roster, web/components/roster-cards.tsx). Mode: Operate.

Audience and job: the one manager, on a phone before kickoff or at a laptop midweek, asking "who starts and why" for every rostered player. Action: set the lineup in ESPN. Proof: the projection, the gap to the alternative, the injury status, the matchup. Constraints: the call and reason come from `season.roster_cards`; the UI adds no judgment. Data can be stale; the stale badge stays.

Chosen direction: broadcast board (user-locked 2026-09-08). Memorable moment: each player cut out on his team's colors, the opponent's logo beside the kickoff, with "at" when he travels.

Unresolved: whether the rest of the app adopts this world next (Week page first).

## Direction contract

THESIS: A roster read like a broadcast lineup graphic, one panel per player, the call legible from across the room. Refuses the shadcn stack of same-gray cards with a title and a paragraph.

OWN-WORLD: Near-black field with team-color player panels (32-team table), cut-out headshots on those panels, faded team-logo watermark behind them, condensed display numerals (Barlow Condensed) for projections and names, Geist for prose, one lime START chip, amber for close calls, red for OUT/IR. Light theme is the same board under press-box light.

STORY: He sees the starters as a lineup, reads the projection and the gap at a glance, notices any questionable tag and any close call, then opens an outlook only when he wants the paragraph.

FIRST VIEWPORT: Title row "Roster" + week + stale badge + regenerate. Then STARTERS: two-column cards on desktop, one column on phone. Each card: team-color panel left with headshot; right: name, pos·team, matchup row (vs/at + opponent logo + kickoff), big projection with source split, call chip, reason line; outlook inline on desktop, behind a toggle on phone.

FORM: Broadcast board, first of three offered; user-locked, no seed (narrow, precisely specified request).

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance.
