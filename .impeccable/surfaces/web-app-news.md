---
version: 1
slug: "web-app-news"
primary_target: "web/app/news"
related_targets: ["web/components/news-feed.tsx","web/components/news-story.tsx","web/components/news-search.tsx"]
---

# News page

Scope: `/news` and `/news/player/<id>` (web/app/news, news-feed.tsx, news-story.tsx, news-search.tsx). Mode: Operate. Added 2026-10-19 (ffl-0gv), from the approved wireframe news-v1.

Audience and job: the one manager, on the phone on game-day mornings and from a push, asking "what happened, and does it move one of mine or a free agent". Action: tap a player for his line, then the dialog or ESPN. Proof: the source and its age, the headline, the chip colour per player (up lime, down red), who holds him. Constraints: effects and lines are Jev's and Haiku's; the UI adds no judgment; owners are read at request time and may be missing.

Chosen direction: the broadcast board, inherited. Memorable moment: the row of player chips under each story, a portrait, a status colour and an owner tag per name, read like a ticker.

## Direction contract

THESIS: A wire ledger of stories on one board card, each ending in a strip of player chips; refuses the news-card grid with thumbnails and the up/down arrow glyphs.

OWN-WORLD: Field, panel, one board card divided by ink hairlines; Barlow for names, tags, chips and the title; Geist for headline and overview; chips washed lime (up) or red (down), the Three Verdicts Rule's one exception (2026-10-19); muted squared chips for Updated / Corrected / Retracted.

STORY: He scans headlines, reads the chips for Mine and FA, taps one for Haiku's line, opens the player or the full story, and follows the search to one player's 30 days.

FIRST VIEWPORT: Title "News", search field and the four filter chips beneath; then the card: source and age, headline, overview, chips; two to three stories on a phone.

FORM: extension of the board (wireframe news-v1, approved); Card, ToggleGroup, Combobox, Popover, Collapsible, Badge. seed: none (unavailable)

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
