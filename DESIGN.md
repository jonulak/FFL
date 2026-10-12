---
name: FFL
description: A one-manager fantasy season cockpit read like a broadcast lineup board.
colors:
  field: "oklch(0.962 0.004 250)"
  field-dark: "oklch(0.135 0.008 260)"
  panel: "oklch(1 0 0)"
  panel-dark: "oklch(0.2 0.01 260)"
  ink: "oklch(0.145 0 0)"
  ink-dark: "oklch(0.985 0 0)"
  ink-muted: "oklch(0.556 0 0)"
  ink-muted-dark: "oklch(0.708 0 0)"
  start: "oklch(0.8 0.2 130)"
  start-foreground: "oklch(0.22 0.06 130)"
  start-dark: "oklch(0.86 0.22 130)"
  start-foreground-dark: "oklch(0.2 0.06 130)"
  close: "oklch(0.86 0.15 85)"
  close-foreground: "oklch(0.34 0.09 70)"
  close-dark: "oklch(0.83 0.16 85)"
  close-foreground-dark: "oklch(0.25 0.07 70)"
  out: "oklch(0.55 0.22 27)"
  out-dark: "oklch(0.7 0.19 22)"
  destructive: "oklch(0.577 0.245 27.325)"
  hairline: "oklch(0.922 0 0)"
  hairline-dark: "oklch(1 0 0 / 10%)"
  team-fallback: "#3f3f46"
typography:
  display:
    fontFamily: "Barlow Condensed, sans-serif"
    fontSize: "2.6rem"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "-0.025em"
  headline:
    fontFamily: "Barlow Condensed, sans-serif"
    fontSize: "2.25rem"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "-0.025em"
  title:
    fontFamily: "Barlow Condensed, sans-serif"
    fontSize: "1.65rem"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "-0.025em"
  section:
    fontFamily: "Barlow Condensed, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.75
    letterSpacing: "0.14em"
  body:
    fontFamily: "Geist, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.625
  reason:
    fontFamily: "Geist, sans-serif"
    fontSize: "15px"
    fontWeight: 500
    lineHeight: 1.375
  label:
    fontFamily: "Barlow Condensed, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "0.12em"
  caption:
    fontFamily: "Geist, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.625
  mono:
    fontFamily: "Geist Mono, monospace"
    fontSize: "0.75rem"
    fontWeight: 400
rounded:
  sm: "0.375rem"
  md: "0.5rem"
  lg: "0.625rem"
  xl: "0.875rem"
  pill: "1.625rem"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "20px"
  section: "28px"
components:
  chip-start:
    backgroundColor: "{colors.start}"
    textColor: "{colors.start-foreground}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "2px 6px"
    height: "20px"
  chip-close:
    backgroundColor: "{colors.close}"
    textColor: "{colors.close-foreground}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "2px 6px"
    height: "20px"
  chip-out:
    backgroundColor: "{colors.out}"
    textColor: "#ffffff"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "2px 6px"
    height: "20px"
  chip-muted:
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "2px 6px"
    height: "20px"
  player-card:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "12px"
  button-outline:
    backgroundColor: "{colors.field}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "0 10px"
    height: "28px"
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.ink-dark}"
    rounded: "{rounded.lg}"
    padding: "0 10px"
    height: "32px"
---

# Design System: FFL

## Overview

**Creative North Star: "The Broadcast Board"**

The UI is one manager's own instrument, read on a phone before kickoff or a laptop midweek, and it is set like a broadcast lineup graphic: a near-black field, one panel per player cut out on his club's colours, the projection in big condensed numerals, and the verdict in a squared lower-third chip. The call reads from across the room; the paragraph is there when he wants it.

The world is established on the Roster page (`web/app/roster`, `web/components/roster-cards.tsx`, `player-media.tsx`, `outlook.tsx`) as of 2026-09-08, and extended the same day to the navigation (`nav.tsx`, `nav-links.tsx`) and the Matchup page (`web/app/page.tsx`, `matchup.tsx`), then on 2026-09-09 to the League page (`web/app/league`, `league.tsx`, `club-logo.tsx`, `week-select.tsx`) and the Waivers page (`web/app/waivers`, `waiver-table.tsx`, `pos-tabs.tsx`), and on 2026-09-13 to the play feed (`play-feed.tsx`, `web/app/plays`); on 2026-09-14 the Matchup page's two lineup cards became one slot-aligned board of shadcn Items, the play strip a compact one-line form, and the club marks joined the scorebug; on 2026-10-11 the NFL tab (`web/app/nfl`, `nfl-board.tsx`, `nfl-game.tsx`, `nfl-feed.tsx`) and the game-line link (`game-link.tsx`) were added. Journal and Draft still carry the stock shadcn neutral look (Geist `text-xl` titles, default pills, muted-footer cards, tables). They have not been brought over yet; the Roster page is the reference when they are. This file records the board, not the leftover.

Everything is a shadcn (base-ui) component. New looks are expressed by extending a shadcn variant (`badge.tsx` adds the verdict colours and a `shape: chip` axis) or by laying a shadcn Card out with grid utilities, never by hand-rolling an element. Tailwind 4, Next 16, lucide icons. Dark follows the system; there is no toggle, and `.light`/`.dark` classes exist only as hooks.

**Key Characteristics:**
- Near-black field in dark, off-white press-box light in light; both are the same board.
- Team-colour gradient panels from the 32-club table are the only saturated surfaces besides the three verdict colours.
- Barlow Condensed for names, numerals and labels; Geist for reasons and prose; Geist Mono for slot codes in tables.
- Three verdict colours (lime START, amber close/questionable, red OUT/IR); nothing else is coloured.
- One card shadow, no decoration beyond the watermark.
- Motion (`globals.css` "Motion"): one authored moment, a play arriving in the feed (its slot opens at the top, the row slides in from its own team's edge, a scoring play leaves a lime wash that fades over 2.6s; the dropped row folds on the mirror curve so the card holds its height). Everything else is continuity on `--ease-arrive` (`cubic-bezier(0.16, 1, 0.3, 1)`): the outlook panel opening; dialogs fading up 12px from 0.97 over 300ms and out in 150ms on `--ease-leave`, over a plain scrim (no backdrop blur, which stuttered on the phone); the season modal waiting up to 180ms for its log so it opens at full size (growing while its entrance ran, it snapped on iOS), and only on a slower log its body growing from the skeleton's height to the log's over 420ms on an even in-out curve (centred, its top travels half the growth; an ease-out would jump), rows entering 28ms apart; inside it every fold animates, a week's breakdown panel over 200ms and a past season's weeks folding cell by cell (a one-track grid run 1fr to 0fr, since a table row takes no height) over 320ms open and 220ms shut. On the phone the claims sheet becomes the review rather than handing off to a dialog: the sheet rises to full height and squares its corners over 420ms (a WAAPI FLIP of its content height) while the claims sink back and fade in 160ms and the review rises 20px into the new room 60ms later; Back runs it the other way. A dragged claim row lifts into an overlay over a dashed slot where it will land, and the group it crosses into takes a 2px ink ring. A lineup move on the Roster page plays out (`use-flip.ts`, a FLIP over page coordinates captured before each re-slot, read-back and revert): after an 80ms beat for the sheet to leave, the player who changed slot travels from his old row to his new one as a lifted card (panel colour, the one card drop, the cards un-clipped while he is between them), his neighbours, any group below and a card that changed height follow on the same clock (320–560ms by the longest trip, `--ease-arrive`), and he lands under an ink wash from his slot that holds and fades over 1.8s (ink, since a move is not a verdict). Reduced motion keeps the fades and the wash and drops the travel.

## Colors

A neutral shadcn field with three verdict colours and the NFL club palette doing all the saturation.

### Primary (verdict)
- **Board Lime `start`** (`oklch(0.8 0.2 130)` light / `oklch(0.86 0.22 130)` dark, on `start-foreground` deep green ink): the START chip only. It is also the page's focus ring and text-selection tint, so the browser's own surfaces belong to the board.
- **Amber `close`** (`oklch(0.86 0.15 85)` / `oklch(0.83 0.16 85)`, on `close-foreground` brown ink): the CLOSE CALL chip, the QUESTIONABLE/DOUBTFUL chip (`warn`: amber at 25% with an amber ring), the source-spread figure (`±3.5`) and the "facts changed since" stamp. In dark, amber text uses `close` itself, not the foreground.
- **Red `out`** (`oklch(0.55 0.22 27)` / `oklch(0.7 0.19 22)`, white text): the OUT and IR chips and the OUT/IR injury chip. `out-soft` (15% fill, 40% ring, red text) exists on the Badge but is not yet placed.

### Secondary (club)
- **Team panel gradient**: `linear-gradient(168deg, primary 0%, color-mix(in oklab, primary 62%, secondary) 100%)` from `web/lib/teams.ts`. No text is ever set on a panel, so club contrast is never a concern; the headshot and the watermark are its only contents. Unknown team falls back to zinc (`#3f3f46` / `#18181b`). Idle players (BYE/OUT/IR) get the panel at 35% saturation and 80% opacity.

### Neutral
- **Field** (`oklch(0.962 0.004 250)` light, `oklch(0.135 0.008 260)` dark): the page background, faintly cool so white/charcoal cards read as panels.
- **Panel** (`oklch(1 0 0)` / `oklch(0.2 0.01 260)`): card surface.
- **Ink** (`oklch(0.145 0 0)` / `oklch(0.985 0 0)`) with opacity steps for hierarchy: 85% for the opponent code, 80% for position and vs/at, 70% for an idle player's name, 75% on 10% for the BENCH chip.
- **Muted ink** (`oklch(0.556 0 0)` / `oklch(0.708 0 0)`): kickoff, source split, outlook prose, stamps, definitions.
- **Hairline** (`oklch(0.922 0 0)` / white at 10%): shadcn borders. On the board, dividers are ink at 10–12% (card footer rule, section rule) rather than `border`.
- **Destructive** (`oklch(0.577 0.245 27.325)`): the shadcn stale badge and error states, shared with the stock pages.

### Named Rules
**The Three Verdicts Rule.** Lime, amber and red mean START, close/questionable and OUT/IR, and nothing else. No other hue is added for state; blue, green or purple accents do not exist here.

**The Club Owns the Colour Rule.** Saturation on the board comes from the team table, never from the design tokens. Text is never set on a club colour.

## Typography

**Display Font:** Barlow Condensed 500/600/700 (`--font-barlow`, via next/font)
**Body Font:** Geist (`--font-sans`)
**Label/Mono Font:** Geist Mono (`--font-geist-mono`), slot codes in tables

**Character:** Condensed, tight, tabular numerals for anything that is a name, a number or a label, so the card reads like a lower-third; Geist at small sizes for the one line that argues. `.font-display` always sets `tabular-nums`.

### Hierarchy
- **Display** (700, 2.6rem, line-height 1, tracking tight): the projection or, once the game is on, the posted score. Muted when a projection is zero.
- **Headline** (700, 2.25rem, line-height 1, tracking tight): the page title ("Roster"), with the week beside it at 600 in muted ink.
- **Title** (600, 1.65rem, line-height 1, tracking tight, two-line clamp, balanced): the player's name.
- **Section** (600, 1.25rem, uppercase, tracking 0.14em): group headings (STARTERS, BENCH, IR) with the count in 500 muted tabular and a hairline running out to the right.
- **Reason** (Geist 500, 15px, leading snug): the one line that explains the call. The only body-weight text set above 14px.
- **Body** (Geist 400, 14px, leading relaxed, max 68ch): outlook prose in muted ink.
- **Label** (600, 13px, uppercase, tracking 0.12em): chip text, the section state (PROJ, FINAL) and the way-onward links. **Unit** (600, 12px, the same tracking): the PROJ/PTS unit on a row numeral, the "vs"/"at" mark and the totals labels; the scorebug unit alone is 14px. Every label size is set in rem (0.8125rem, 0.75rem, 0.9375rem for the nav) so the whole board follows the reader's text size together.
- **Caption** (Geist 400, 12px, 16px leading on the board): kickoff, source split, stamps, run status. Numbers in captions are `tabular-nums`.

### Named Rules
**The Condensed-for-Facts Rule.** Names, numbers, units and verdict labels are Barlow Condensed; sentences are Geist. A number set in Geist or a sentence set in Barlow is off-system.

**The Unit Beside the Number Rule.** A big numeral always carries its small uppercase unit (PROJ or PTS) on its baseline; the number never stands alone.

## Layout

One centred column, max 64rem (`max-w-5xl`), 12px side padding, 16px top. A sticky 48px top rule (field at 92% over a blur, bottom hairline in ink at 12%) holds the FFL wordmark, the six page tabs and the Refresh button.

Roster page: title row (title, stale badge, action button pushed right, `gap-x-3`), then groups spaced 28px apart. Each group is a section heading and a card grid: one column on the phone, two from `md` (768px), 12px gutters.

Player card: a CSS grid of `5.25rem` panel column (6.25rem from `sm`, 640px) and a fluid right column, three rows (header / content / footer) with the panel spanning all three. Card spacing is 12px on the phone, 16px from `sm`. Header holds name and pos·club left, the chip stack right (aligned end, 4px gap). Content holds the matchup row, then the numbers 12px below, then the reason 8px below. Footer holds the outlook.

Phone versus laptop: the outlook paragraph is inline from `md`; below it, the same text sits behind a collapsible trigger so the first screen holds the cards. The definition caption is hidden below `md` for the same reason.

**The Decision Above the Fold Rule.** On a phone the card shows name, matchup, projection, chip and reason before anything that does not change the call; prose is behind a tap.

## Elevation & Depth

Tonal layering with one shadow. The field is darker than the panel (or, in light, faintly cooler than white), and that step is most of the depth. Every player card carries a single soft drop, `0 10px 28px -16px rgb(0 0 0 / 0.55)`, so panels lift off the field the way a graphics stack does. Inside a panel, depth is a `1px` inset white keyline at 8% on the panel's right edge, a headshot drop shadow (`0 8px 12px rgb(0 0 0 / 0.5)`) and a watermark of the club mark at 16% opacity, oversized and trailing off the left edge. Cards have no ring other than shadcn's `ring-1 ring-foreground/10`. Nothing glows and nothing changes shadow on hover.

**The One Shadow Rule.** The card drop is the only box shadow on a surface. Chips, buttons, tables and headings are flat.

## Shapes

Two corner registers. Surfaces are gently rounded: cards 10px (`rounded-lg`, the shadcn `--radius`), buttons 10px, small buttons 12px-capped. Verdict chips are squared, 6px (`rounded-sm`), in deliberate contrast to the default shadcn pill (`rounded-4xl`) which remains on stock badges (stale, injury in tables). The team panel is clipped flush to the card's left edge with no inner radius; the headshot is a transparent cut-out standing at the panel foot, scaled past the panel width (210%, 172% from `sm`) and centred. Dividers are 1px hairlines in ink at 10–12%, never the shadcn `border` token on the board.

## Components

### Verdict Chips (Badge, `shape="chip"`)
- **Character:** a lower-third block; squared, condensed caps, letterspaced.
- **Shape:** 6px corners, 20px tall, 6px side padding.
- **Variants:** `start` lime on green ink, naming the slot the call puts him in (QB, RB, WR, TE, FLEX, D/ST, K; STRATEGY.md "Slot by kickoff"), read out as "start at"; `close` amber on brown ink (CLOSE CALL); `warn` amber at 25% with a 60% amber ring, brown ink / amber text in dark (QUESTIONABLE, DOUBTFUL); `out` red on white (OUT, IR, injury OUT/IR with the return week); `muted` ink at 10% on ink at 75% (BENCH, BYE, SIT: sat by hand, not a verdict colour). A SIT row desaturates like an idle one. `out-soft` is defined and unplaced.
- **Stack:** top-right of the card header, verdict first, close call second, injury third.
- **Out of place (Roster):** a row ESPN has somewhere the call does not (`isMismatch`) is washed amber (`close` at 8%, 6% in dark) and its slot button faced amber (border `close`, fill at 25%/15%, brown ink / amber text), beside the chip's amber ring. In his swap sheet the move that puts him where the call wants him (`lineup.recommendedTarget`: his call slot, else the bench; a partner the move also puts right wins, then the gain) stands first under a THE CALL label in the label style, amber-tinted with an inset amber ring and a hairline below; an empty slot's sheet leads with the man the call names for it.

### Player Card (shadcn Card, board layout)
- **Corner Style:** 10px.
- **Background:** panel colour; footer is transparent with a top hairline (overrides shadcn's muted footer).
- **Shadow Strategy:** the one card drop (Elevation).
- **Border:** shadcn's 1px foreground ring at 10%.
- **Internal Padding:** 12px phone, 16px from `sm`; header top 10–12px, footer bottom 12–14px.
- **Team panel:** club gradient, watermark, headshot; D/ST and missing portraits show the club mark at 72% width with a drop shadow instead. Idle players desaturate the panel.
- **Matchup row:** `VS`/`AT` label, 20px opponent logo (ESPN `500-dark` cut under a dark scheme via `<picture>`), opponent code, `·`, kickoff in local time.
- **Numbers:** display numeral + unit; beside it the source split in caption (`ESPN 16.4 · Sleeper 15.7`, or `ESPN only`) and the spread in amber when 3 or more.

### Outlook (Collapsible)
- **Laptop:** paragraph in muted body, 68ch max, stamp beneath.
- **Phone:** the stamp is the trigger (min 32px tall, 12px text) with a lucide `ChevronDown` that rotates 180°; the panel animates height over 300ms on `cubic-bezier(0.16, 1, 0.3, 1)`. No fade is stacked on top. The Roster page's other motion is the lineup move (Overview, Motion).
- **Age on the collapsed row:** under each row's disclosure chevron, inside the same button, the outlook's short age (`now`, `12m`, `3h`, `2d`) in Barlow 600 at 0.6875rem, tabular: muted when current, amber (the "facts changed since" colour) once the card's facts have moved on, red when the last rewrite failed, a 12px spinner while one is being written, nothing before the first. It runs on one page-wide minute clock (`useNow`, shared with the stamp's "ago"), so a page left open stays true.

### Section Heading
- Condensed uppercase title, muted count, hairline (`bg-foreground/12`) filling the rest of the line, baseline-aligned.

### Scorebug (Matchup, shadcn Card)
- Three-column grid (side, `VS`, side) on three subgrid rows (club / total / note) so both sides align row for row when a name wraps. The club line is the `ClubLogo` (40px on the phone, 48px from `sm`) at the card's outer edge with the team name beside it in Title (1.375rem on the phone so it never reads as a numeral, 1.65rem from `sm`), so the two marks frame the bug; the total in Display (2.6rem, 3.25rem from `sm`) with its PROJ or PTS unit, at the outer edge on the phone and, from `sm`, at the inner edge so the two totals meet at VS and the comparison is one glance while the marks and names hold the corners (the totals then stand over the board's number columns); once a starter's game is on the caption under it reads `proj 156.4 · 10 to play` (or `· final`), and "by projection 156.6 (+0.5)" follows when the optimal lineup differs. The opponent side is mirrored (mark outermost, text right-aligned). Same card drop as the player card.

### Lineup Board (Matchup, shadcn Card of shadcn Items)
- Both lineups on one card, slot for slot: every line is a three-column grid of my half, the slot code (Geist Mono 12px muted, centred, FLEX for ESPN's RB/WR/TE; 1.5rem wide with 6px gutters on the phone, 2.5rem and 12px from `sm`) and the opponent's half, so QB sits beside QB and FLEX beside FLEX at every width, the phone included. The same grid carries the header, the totals, the group labels and the footer, so the slot column is one spine from top to bottom. Bench and IR pair by position in the list under a 13px label with a hairline at each outer edge, and a shorter side leaves its cell empty.
- A half is a shadcn `Item` (`size="sm"`, border and padding removed): `ItemMedia` is the 1.75rem (3rem from `sm`) `TeamPanel` tile with 6px corners at the outer edge; `ItemContent` is the name block; `ItemActions` is the number beside the spine, pinned to the row's top so the two numbers being compared share an edge whatever wraps beneath (from `sm`; on the phone the number is a line inside the block, `24.7 PTS · proj 20.7`, at 1.25rem, and the text column is 132px, wide enough for that line and the score line to stay whole). The opponent's half is the same Item reversed (`flex-row-reverse`, content right-aligned, the title line reversed so chips sit toward the spine), so the two sides face each other. `line-clamp-none` on an Item title or description resets it to `display:block`; a flex title takes `flex!`.
- Name in condensed 600 at 1.125rem (1.25rem from `sm`) with the injury chip (`warn`/`out`; on the phone the status alone, IR for injury reserve, with `back wk 3` as its own caption line, because `DOUBTFUL · WK 2` is wider than a half column) and a muted BYE chip inline; beneath, a caption `POS · TEAM · vs/at · logo · OPP` where the dot, `vs`, mark and code are one unbreakable unit, so a phone column breaks to `QB · PHI` / `· vs WSH` and never orphans the code; then the kickoff (or BYE / "not scheduled") on the caption line directly beneath, no gap, since the two are one group; the stat line stands 8px below them, the row's one fact set apart from its identity. **The phone half is set like a broadcast app's matchup row** (the reference was ESPN's, 2026-09-14): no photo column; the name shortened to initial and surname (`B. Purdy`, `A. St. Brown`; `shortName` in `web/lib/format.ts`) with the club tile inline after it at 24px and 4px corners, at the outer edge; the matchup and the game on one caption line, `AT LAR · 27-7 Final` (`Kickoff compact`: score with his own club first, then the status; the clock and score while the game is on; the time before it), no position or club since the spine and the tile say them, and no opponent mark; the lead stat group only (`25/34, 205 yds, 3 TD, 1 INT`) as the Item's footer, so it has the whole half including the space under the number column and wraps at its commas rather than clipping, the whole line as its title; the number at 1.25rem in its own column beside the spine with its unit beneath it rather than beside (the column is too narrow for both), then `proj 16.5` once posted. Six starters follow the scorebug on the first screen. A stacked full-width form was tried the same day and rejected; the answer was fewer facts per half, not more room. Once his game is on that line becomes the clock and the score, `Q2 9:09 · JAX 14, CLE 3` with his own club first (the clock in medium ink at 85% while in progress; `Half`, `OT`, then `Final` in caption muted; dot and score one unit), and beneath it his box-score line (`stat-line.tsx`: Geist 13px at 85% ink, the play feed's register for the one fact in a row; a wrapped line leads with the `·`; on the board `wrap` lets a group break at its commas, each `5 car` still one piece, because a D/ST group is wider than a half column). The number is condensed 700 at 1.5rem and always carries its own 11px unit on the baseline (the Unit Beside the Number Rule), because through a game day the rows are a mix: `14.5 PROJ` for a game still to come, with a spread of 3+ in amber beneath; `0.5 PTS` once his game is on, with the projection it replaced beneath (`proj 15.1`) and, while the game is in progress, the live dot and "live" before it. Game phase is read from ESPN's `game_played` (final), posted points, or the kickoff clock (`web/lib/game.ts`). Lines divide with ink at 10%. Idle players desaturate the tile; a player done for the week (idle, or his game final by the feed's `post` or ESPN's `game_played`) steps his name, and only his name, to 40% ink (below 4.5:1 on purpose: a finished name is meant to recede), so the names still in play stand out. While his game is on the half carries a wash, a gradient from the spine's far edge (full at the name, 35% by the number; `globals.css` `.field-half`): ink at 7% in the game but off the field, `--onfield` orange at 24% with his unit on the field, `--redzone` red at 24% in the red zone (`web/lib/field.ts`, read off possession); halves still to play or final stay bare, so the board shows at a glance who is playing right now. Colour alone on screen; the button's label says it to a screen reader.
- The card header is the section heading style at each end: "MY LINEUP" with my state beside it, the opponent's state and name (ESPN's abbreviation below `sm`) at the right, in the label style: PROJ before any starter's game is on, `4 TO PLAY` (starters not yet final, idle ones excluded) once one is, FINAL when none remain. The footer (transparent, top hairline) lists each side's by-projection swaps in its half: slot in mono, out → in, gain at the outer end; the opponent's mirrored (gain, swap, slot, right-aligned).
- Under the starters, each half rules off its totals with a 2px rule at ink 35% (the same cut rule as the standings), the two rules meeting at the spine: three cells, an 11px label over a condensed 700 numeral at 1.25rem: SCORED (points posted, final and in-progress games), TO PLAY (the projection still to come: starters yet to kick off, idle ones excluded, plus what a player mid-game has left of his projection) and EST. FINAL, their sum; the estimate alone is full ink, the parts 70%, and the opponent's cells run the other way so the estimates meet in the middle. On the phone only EST. FINAL is a numeral; `scored 45.2 · to play 98.1` steps to a caption beneath it. A player mid-game is carried at his projection until his posted score passes it.

### Play feed (Matchup strip and `/plays`, shadcn Card)
- The ledger of every points change for a starter in the matchup, newest first, derived from the live feed (`ffl/plays.py`). A row is a shadcn `Item` in an `ItemGroup` (border and padding removed, rows divided with ink at 10%): it is the lineup row turned to face the play: a 2.5rem (3rem from `sm`) `TeamPanel` tile as the media, the name in condensed 600 at 1.125rem with `POS · TEAM` in caption beside it, the play text where the reason line would be (Geist 13px at 85% ink), and beneath it the caption `1:48 PM · Q2 14:14` in local time and game clock. The number is the delta, condensed 700 at 1.5rem with its 11px PTS unit on the baseline, and `now 6.9` in caption beneath, the way a posted score carries the projection it replaced. A loss steps the numeral to 70% ink; red stays OUT/IR.
- My rows hang from the card's left edge and the opponent's from the right, mirrored rather than merely aligned (the Item reversed: tile and numeral swap ends, the name line runs right to left), at 88% of the card's width on the phone and 74% from `sm`, so the side reads from the column before the name. No per-row card.
- **Compact row (the Matchup strip):** one line per play, 1.75rem tile with 4px corners, the name (condensed 600 at 1.125rem, leading 1, the board name), then the game clock `Q4 0:19` in caption and the delta at 1.25rem with its PTS unit at the inner end, `now 24.7` in caption beneath it. No play text (it is the row's `title` and the `/plays` page's), no stamp: the strip says who moved, when, by how much and to what. Mirrored the same way at 85% of the width (82% from `sm`) so the hang reads at one line tall, 6px row padding, so five plays stand about a third as tall as the full rows.
- The card header is the section heading style with the hairline running out to the right and, at the line's end in the label style at 55% ink, the way onward: ALL PLAYS → on the Matchup strip (the latest five in compact rows at every width, between the scorebug and the lineup board, absent until the week's first play; the link's tap target is padded to 36px), MATCHUP → on the `/plays` page, whose heading carries the row count in muted condensed. Empty state on `/plays`: one muted sentence. Same card drop as the player card.

### NFL game card (`/nfl`, `nfl-board.tsx`, shadcn Card)
- One card per NFL game, two to a row from `md`, grouped under condensed label headings by kickoff window (LIVE first, then each window in the viewer's zone, "Time TBD" last; the heading renders blank on the server and fills in once mounted, so there is no zone to mismatch), my games first inside a window; clubs on bye close the page as one `Bye · …` label line. The whole card is one link to the game page (hover tints it ink at 2.5% and lifts its ring, focus is the 2px ring, a chevron at the header's end nudges right on hover). Header band (ink at 4%): state at the left in the label style (the live chip, `Q2 9:09`, the kickoff and network before it, FINAL in full ink), and at the right a muted chip naming whose starters play in it (`2 mine · 1 opp`) before the chevron. A card with my starters in it carries the 30% ring the League scoreboard gives my own matchup.
- Body: away over home, each a 36px club mark, name at 1.25rem condensed 600 with the record in caption beneath, the score right in condensed 700 at 1.5rem. Once the game is on the trailing club steps to 60% ink; a final's winner carries the 10px solid caret (the League tile's mark) and the loser keeps its column empty. The club with the ball takes an 8px `--onfield` dot after its name, `--redzone` inside the 20, a screen-reader label either way. A stale feed shows a `StaleBadge` beside the title; a failed fetch keeps the last good board. Polls every 30 s.

### NFL scorebug (`/nfl/<week>/<club>`, `nfl-game.tsx`, shadcn Card)
- The Matchup scorebug turned to clubs: three columns (away, score, home), the 44px mark at the outer edge with the name (the club's abbreviation below `sm`, so two-digit scores never truncate it) in Title (1.375rem, 1.65rem from `sm`) and the record in caption, the two scores meeting at the centre in Display (2.6rem, 3.25rem from `sm`) split by a 30% ink en dash, the trailing score at 60%. Before kickoff the centre is a muted `vs`. Under it one centred line: kickoff and network, or the clock and score state (medium ink at 85% while on) with `GB ball, 2nd & 7` after it; the possession dot sits by the club's name as on the card. The `compact` form (the feed page's) drops to a 32px mark, `text-lg` names and a 2rem numeral.
- Below it, "In this game" is a board card of my starters in it (left) facing the opponent's (right, mirrored): short name and the points on one line, the slot and club (`QB · GB`) in caption on a line of their own so a surname never truncates, the points at 1.25rem condensed 700 with a PTS unit, the stat line beneath (the Matchup board's `StatLine`, wrapping at its commas), rows paired by order; absent when none of either side plays. Then "Scoring plays" (the heading's count is the game's, the card shows the latest five) and `All plays →` at the heading's end in the label style at 55% ink (tap target padded to 40px; it opens the feed on All). Polls every 15 s.

### NFL play feed (`/nfl/<week>/<club>/plays`, `nfl-feed.tsx`, shadcn Card + ToggleGroup)
- The game's every play, drive by drive, newest drive first. A back link (`← Game`), the compact scorebug, then a `ToggleGroup` (All / My players / Scoring, condensed 600 uppercase) whose value lives in the URL (`?f=`, `router.replace` without scroll, 60% opacity while it transitions) with the play count at the far end. One board card, each drive under a header band (ink at 4%): the club in the label style, ESPN's summary in caption (`10 plays, 91 yards, 6:13`) and how it ended (`TD`, `PUNT`) at the end; a drive still on the field has no result.
- A play is the Play-feed row without a tile: the caption line carries the clock (`Q2 14:14`, `OT`), the down and distance, and the fantasy players in it by name (medium ink at 85%), then the play text in Geist 13px at 85% ink. A marked play hangs one 3px rail on its left, lime for my starter's and ink at 60% for the opponent's (the left edge is always 3px, transparent when unmarked, so the text column never changes width). My players keeps my starters' plays only; the opponent's are marked in the second ink under All. A scoring play is washed `start` at 7% and leads with its score line in one fixed order, away then home, both clubs named (`CHI 7 · GB 14`), condensed 700, the scoring club's part in full ink and the other at the caption's muted ink; a turnover is an amber `close` chip and a penalty a muted FLAG chip, both on the caption line inside the row so they slide in with it. Arrivals are the Motion moment: one ledger over the whole game (a ledger per drive would treat a new drive's first play as already there, and one over the filtered list would replay what the filter hid), the new row's slot opens at the top and the row slides in, a scoring one leaving the lime wash. Empty states are one muted sentence each, per filter. Polls every 15 s.

### Game link (`game-link.tsx`)
- A game line (`Q2 9:09 · JAX 14, CLE 3`, `AT LAR · 27-7 Final`) on the Matchup board, League boards, Team page and Roster cards is a link to that NFL game. The text keeps its ink and only gains a 30% ink underline on hover; the link stands above the row's own tap target (`relative z-10`) and pads itself to a 40px target with matching negative margin so the line does not move. Rendered as plain text when the game is not known.

### Live chip
- `muted` chip with a 6px ink dot: "LIVE" while the week is in play. Not a verdict colour.

### Buttons (shadcn, unchanged)
- **Shape:** 10px; `sm` 28px tall, 10px side padding, 0.8rem text.
- **Outline** (`Regenerate outlooks`, `Refresh`): field background, hairline border, muted on hover; in dark, input at 30% fill.
- **Primary:** ink on inverse ink. **Ghost / Destructive / Link:** shadcn defaults.
- **Focus:** the global `focus-visible` outline is 2px `start` at 2px offset.

### Navigation (`nav.tsx`, `nav-links.tsx`)
- **Wordmark:** "FFL" in Barlow 700 at 1.6rem, tracking tight, linking to the Matchup page; a 1px ink hairline (12%) stands between it and the tabs.
- **Tabs:** the label style at 15px (condensed 600, uppercase, 0.12em), 10px side padding, filling the 48px bar so the tap target is the full height. Inactive tabs are ink at 55%, 85% on hover; the current page (`aria-current="page"`, from `usePathname`) is full ink with a 2px ink bar flush to the bottom rule. No background on hover, no pill.
- **Phone:** the strip scrolls horizontally with the scrollbar hidden and a 28px fade at its right edge; the current tab is scrolled to centre on arrival. Refresh stays pinned at the right edge at every width.
- **Colour:** ink only. The active bar is ink, not lime; lime remains START.

### Standings (League, shadcn Table inside a board Card)
- One full-width card, the table flush to its edges. Header cells in the label style; rows divide with ink at 10%; a 2px rule at ink 35% under the last playoff seed marks the cut. Rank in condensed 600 muted; team cell is a 32px `ClubLogo` beside the name in Title-weight condensed at 1.25rem with the abbreviation in caption beneath; W-L in condensed 700 at 1.25rem; PF condensed 600, PA and streak condensed 500 at 70% ink. My row is ink at 5% with a `muted` ME chip after the name. Below `sm` the PA and streak columns hide (the streak joins the caption) so the phone table needs no scroll. No hover tint.

### Win meter (`win-meter.tsx`, Base UI Meter)
- The odds as a tug of war from even, ink only (Three Verdicts Rule: the favourite reads off the fill's direction and length, never a hue): a track of ink at 12% with a 2px centre line at ink 45%, and an ink fill growing from that line toward the favourite's end by its margin over 50% (71% fills 21% of the width; a 50–50 game shows the line alone). A final fills its whole half to the winner, on the scorebug and on every League tile; the result is read off the two scores as shown (`finalP`), not the model. The fill's start and width move over 600ms on `--ease-arrive`; reduced motion drops it. Base UI's Meter rather than shadcn's Progress, so it is `role="meter"`, labelled with the favourite and its share (or the winner); the fill is drawn by hand because Meter's Indicator only grows from the left edge.
- **Scorebug:** a fourth row spanning the bug under a hairline, 6px rounded bar, my % at its left end and theirs at the right in condensed 700, the favourite's in full ink and the other at 60%; on the phone the two numbers sit above the bar so it keeps the bug's width. WIN PROB unit under it; once both sides are final the ends read W/L (or T) and the unit FINAL.
- **League tile:** the same meter turned on its side, a 6px stripe at the card's left edge beside the two team rows, with no numbers. It is drawn in two halves, one inside each row (`OddsHalf`), so its centre is the divider between the rows however tall either runs: the fill grows from the divider up through the away (top) row or down through the home row, by the margin over even. A final tile keeps it, the winner's row filled solid. The card's header (state label and unit) is a band tinted ink at 4%, and the two rows run from its edge to the card's foot, so the stripe meets an edge at both ends with the divider at its middle. A visually hidden Base UI Meter (`TileOdds`) carries the reading for screen readers.

### Header band
- A strip of labels over rows of data sits on a band of ink at 4%, flush to the card's edges: the League scorecard's FINAL / PTS line, the Matchup lineup board's MY LINEUP · PROJ … PROJ · TEAM line, and the standings table's column heads. A header that is a title or the card's own content (a Roster card's player name, the play feed's titled rule) never takes one.

### Matchup card (League scoreboard, shadcn Card)
- One card per matchup, two to a row from `md`. Header row: state label left (FINAL, the live chip, MATCHUP, or UPCOMING for a week with no projection yet), unit label right (PTS or PROJ). Body: away over home, each a 36px `ClubLogo`, name at 1.25rem condensed 600 with the ME chip, record in caption, and the number right in condensed 700 at 1.5rem (score with `proj` beneath once points post; a muted `—` when there is no projection). Once points post the trailing side steps to 60% ink. A finished game (every week before the current one, and in the current week once every starter on both sides is done: ESPN's `game_played`, or sooner the feed's whistle; `season.matchup_final`) reads FINAL in full ink rather than the muted label, and the winner's score carries a 10px solid caret pointing at it, the broadcast scoreboard's mark for a result; the loser keeps the caret's column empty so the scores stay aligned. The live chip marks a tile only while a starter on either side is in a game in progress. The card holding my team carries a ring at ink 30% instead of the default 10%.

### ClubLogo (`club-logo.tsx`)
- A fantasy team's mark as a shadcn `Avatar` squared to 6px corners (the round keyline removed): the `AvatarFallback` is an abbreviation tile (ink at 10%, condensed 600 at 11px) drawn until the `AvatarImage` has loaded, so a missing or slow file never shows a broken glyph. An owner's own upload is served by ESPN only with the league cookies, so the API fetches it (`/logo/{team_id}`, a day's cache) and `clubLogoSrc` (`web/lib/teams.ts`) points at that route; ESPN's stock marks load directly. Sized by the `size` prop: 32px in the standings, 36px on the League scoreboard, 40/48px in the Matchup scorebug.

### Week select (`week-select.tsx`, shadcn Select)
- Sits at the end of the SCOREBOARD heading line, after the hairline, with the live chip beside it; the week applies to the scoreboard only, so the page title is plain "League". The `sm` trigger with the label style (condensed 600, 15px, 0.12em) reading "WEEK n"; items in body Geist with a muted "now" beside the current week; the popup anchored to the trigger's end. Choosing navigates to `?week=n` and the trigger dims to 60% while the page is in flight.

### Position tabs (`pos-tabs.tsx`, shadcn Tabs, line variant)
- The chyron strip under the Waivers title: six tabs (QB RB WR TE D/ST K) in the label style at 15px, 10px side padding, filling a 40px list with a hairline (`border-foreground/12`) along its foot. Each tab renders as a Next Link (`nativeButton={false}`, `render={<Link/>}`) because the position is server state in `?pos=`; there are no panels. Inactive tabs are ink at 55%, 85% on hover; the current position is full ink with the line variant's 2px ink bar sitting on the hairline, the same treatment as the page tabs in the nav. No background on hover, no pill.

### Free-agent table (Waivers, shadcn Table inside a board Card)
- One full-width card, the table flush to its edges, one row per free agent in the API's `add` order (never re-sorted). Header cells in the label style, the ADD column marked `aria-sort="descending"`; rows divide with ink at 10%; no hover tint.
- The row is the Matchup page's lineup row with the waiver numbers beside it: a 36px (40px from `sm`) `TeamPanel` tile with 6px corners; the name in condensed 600 at 1.25rem with the chip stack inline (lime START with the lineup gain, `+0.2`, when he would enter this week's optimal lineup; `warn`/`out` injury chips with the return week; the muted BYE chip); beneath, the caption `POS · TEAM · vs/at · logo · OPP · kickoff`. Idle players (bye, OUT/IR/SUSP) desaturate the tile and step the name to 70% ink.
- Numerals are condensed and right-aligned: `add` in 700 at 1.5rem, full ink when positive and 70% ink otherwise, so the one claim worth making reads first; `wk` in 600 at 1.125rem with `ESPN only` or an amber spread of 3+ beneath. Everything after them is a secondary numeral, 500 at 1.125rem and 70% ink with 8px gutters, in two **stat blocks** (2026-09-21): REST OF SEASON (ROS with its source spread beneath in an 11px line, amber from ±20, else `1 src` / `season`; Δ roster; Δ pool) and LAST 3 GAMES (L3 with `2 games` beneath while the window is short, beat, opp with a 12px lucide arrow for the trend, snap with `1 of 2` beneath when a game has no snaps yet, tgt/snap, ypt, pace). A block heading is the label style at 11px and 55% ink over a hairline the width of its block; the block's first column carries a left hairline at ink 10% and a 16px gutter, so the row squints into identity, decision, projection, form. A position's table carries only the form columns any of its rows has (a kicker: L3, beat, opp, pace); a player's empty cell stays empty, never a dash. The name column takes every pixel the numerals leave (`w-full max-w-0`) and truncates; at the `lg` container width that is about 230px, enough for a name and a chip on two lines.
- **Every column at every width; the table scrolls.** Below `xl` the table is a panel no taller than the viewport under the nav (`max-h-[calc(100dvh-3rem)] overflow-auto`) that scrolls both ways: the two header rows pin to its top (`sticky top-0` / `top-7`) and the tile and the name cell (11rem, a hairline at its right edge) pin to its left, all on the card's own background, so the name stays beside whichever numerals are under the thumb. From `xl` the row fits the page, the panel is the page again (the card drops its `overflow-hidden`, the header row rounds its own top corners) and the headers pin under the 48px nav (`top-12` / `top-[4.75rem]`). Folding the figures into caption lines or a second row per player was tried the same day and rejected in favour of scrolling (2026-09-21).
- **The comparison dropdown.** A chevron (lucide, 32px target, rotating 180° on the outlook panel's ease) stands before the tile; a tap opens my players at his position under the row, every RB, WR and TE for a flex-eligible player, by ROS best first, IR included. A comparison row is the same row tinted with ink at 3% (its pinned cells mix the same 3% into the card colour), the tile 32px and stepped in 12px, a muted chip with his ESPN slot (`bench`, `FLEX`, `RB`, `IR`) where START would be, and in the decision column the free agent's ROS minus his in 600 at 1.125rem, full ink when the claim is an upgrade on this man. The deltas stay empty on a comparison row; they are a free agent's numbers. On the phone the pinned name column narrows to 9.5rem so two numerals still show beside the chevron and tile.
- **Sortable by heading.** Rows open in the API's `add` order; every column heading is a button the height of the header (`SortHead`): a tap sorts by that column, descending first for a number and alphabetical for the name, a second tap flips it; the active heading is full ink with a 12px lucide arrow after the label and `aria-sort`, the others stay label ink with no arrow so they keep their width. A player without the number sorts last either way (`lib/waiver-sort.ts`). The definition caption under the title is a laptop read (`md` and up).
- Empty pool: one muted sentence, "ESPN lists no free agents at TE right now."

### Transactions (`/transactions`, `transactions.tsx`, shadcn Card + Tabs + Toggle)
- The league's wire, newest first, one board Card per fantasy week under a section heading (`WEEK 3 · this week`, Preseason for week 0) with the move count. Title row: headline with the total in muted 600, stale badge, and a `My team` Toggle pushed right (outline, label style; pressed is ink on inverse). Under it a line-variant Tabs strip, the Waivers chyron treatment, filtering client-side: ALL, WAIVERS, FREE AGENTS, TRADES, each with its count in muted 500.
- **Add/drop line:** from `sm` a three-column grid (team 15rem, ADDED, DROPPED) whose names sit once in the card's header in the label style at 12px; on the phone the team over its player lines, stacked. Team cell: 36px `ClubLogo`, the team name (opens `/team/<id>`) in condensed 600 at 1.125rem with the ME chip, then the kind (WAIVER CLAIM / FREE AGENT / DROP, label style at 12px, 75% ink) and the local stamp `Wed, Sep 23 · 3:28 AM` in caption. Each pair is one line across the two player columns (ESPN keeps no link between a claim and its cut, so several pair in listed order; an empty half says NO ADD / NO DROP at 35% ink on the laptop and is left out on the phone). A player line: a 20px glyph square (lucide Plus on ink 12%, Minus on ink 6% at 55%), the 40px (44px from `sm`) `TeamPanel` tile, the name in condensed 600 at 1.125rem and `POS · logo TEAM` in caption. What left a roster is stepped back like an idle player: desaturated tile, name at 70% ink. No colour says add or drop (the Three Verdicts Rule). My moves are washed in ink at 4%.
- **Trade:** the TRADE muted chip and stamp, then the two clubs (40px marks) facing each other across a 28px round swap mark (lucide ArrowLeftRight on ink 10%), each over RECEIVES and what it received; the right half mirrored to the right edge, its caption still reading `POS · TEAM`. A week of trades alone drops the column header.
- A player ESPN gives no club stands on a plain ink-10% tile (portrait, else a lucide UserRound), never on a guessed club colour. Empty filter: one muted sentence.

- **In both player modals** (the season modal and the Matchup breakdown render one `PlayerRecord`, so their data sections cannot drift): under PAST SEASONS, a TRANSACTIONS section (the modal's own section heading, the count as its note) lists his moves this season, newest first, and his draft pick last: a 32px `ClubLogo`, the team name in condensed 600 at 1.0625rem (a `DialogClose` rendered as the team link, so it closes onto that roster), beneath it in caption the other half of the pair (`for Michael Mayer`, `from My Njigbas for DeVonta Smith`) or where the pick fell (`Round 4 · Pick 6 · #36 overall`), and at the right the kind as a muted chip (DROPPED, FREE AGENT, WAIVER CLAIM, TRADED, DRAFTED) over `Sep 18 · Wk 2` / `Sep 7 · Draft`. Fetched with the log on the same tap (`fetchLog`) and drawn only once the log is in; undrafted and never moved, no section.

### Stock surfaces (Journal, Draft)
- shadcn Table, Tabs, Card (muted footer), Input, Textarea, Dialog, Skeleton as shipped; pill badges `secondary`/`outline`/`destructive` for injury tone; `text-xl font-semibold` Geist titles. These predate the board and are to be converted to it, not extended.

## Do's and Don'ts

### Do:
- **Do** express a new look as a shadcn variant (as `badge.tsx` does with `start/close/warn/out/muted` and `shape: chip`) and lay shadcn Cards out with grid utilities; the component library is the only element vocabulary. A row with a tile, a name block and a number is a shadcn `Item` (media / content / actions), a mark over a fallback tile is an `Avatar`; strip their stock border, padding and pill radius rather than rebuild them from divs.
- **Do** set names, numerals, units and chip labels in Barlow Condensed with `tabular-nums`, and sentences in Geist.
- **Do** put the club's colour in the panel from `web/lib/teams.ts` and load marks and portraits from ESPN's CDN with the logo-then-nothing fallback; never a broken-image glyph.
- **Do** keep the stale badge and the "facts changed since" stamp; stale is a state, and every surface says when it was last true.
- **Do** bring a page onto the board by reusing the Roster page's tokens and chips (a Matchup row is a smaller card, not a new colour scheme).

### Don't:
- **Don't** add a fourth state colour or use lime, amber or red for anything but START, close/questionable and OUT/IR.
- **Don't** set text on a team-colour panel.
- **Don't** stack shadows, add hover lifts, or put a shadow on a chip or button; the card drop is the only one.
- **Don't** use the pill badge for a verdict on a board surface; verdicts are squared chips.
- **Don't** add a theme toggle or a theme provider; dark follows `prefers-color-scheme`.
- **Don't** hand-roll a div-and-span element when a shadcn primitive exists.
