# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One user: Jonathan, the manager of team 7 ("My Team") in a private 10-team ESPN
fantasy football league (2026 season, 2-QB starting lineup, full PPR). Nobody else in the league
ever sees the UI. No public write-up is planned.

Usage scenes, all confirmed:
- **Phone, Sunday morning / game day.** Lineup checks and injury news on a small screen, often one-handed, under time pressure before kickoffs.
- **Phone, Tuesday/Wednesday waiver window.** Scanning the add table and deciding claims.
- **Laptop, midweek sit-down.** Reading the journal, standings and outlooks; considered decisions at a desk.
- **Laptop, alongside the ESPN site.** The UI in one window while acting (setting lineups, submitting claims) in ESPN in another. The UI advises; ESPN is where the action is taken.

## Product Purpose

A season cockpit for one fantasy team. It turns two projection sources, the league's scoring and
the live ESPN roster/waiver state into a start/sit call and a waiver ranking, shows the reason
beside every call, and keeps a durable record of every decision and result.

**Success is making the call fast.** Start/sit and waiver decisions answered in seconds with the
reason visible. The record (journal, results, standings) is protected second; when the two
compete for a screen, the decision wins.

The same repo also holds the pre-draft board and live draft assistant (CLI tools; the draft is
done, 2026-09-07, from slot 5). The web UI's Draft page is now a recap surface.

## Positioning

Not a general fantasy app. Every number is scored to *this* league's settings (2-QB, PPR, 4-pt
pass TD) and averaged across ESPN and Sleeper, which neither source does for a 2-QB league. The
decision rules are written down in the league's strategy (`uv run python -m ffl.strategy show`) and the code computes facts against them; the
UI shows the rule that fired, not a black-box grade. Nothing needs to be persuasive or general:
it is the one manager's own instrument.

## Operating Context

- **Cadence:** Tue/Wed waivers → Thu/Sun/Mon games → results sync. Weekly rhythm for 17 weeks plus playoffs.
- **Production:** self-hosted: one Docker Compose stack (Postgres, API, web, a cron jobs container) on the owner's machine, signed in with username and password; Jonathan runs his own copy of the same images.
- **Data flow:** FastAPI (`ffl/api.py`, 127.0.0.1:8300) serves pure computations from `ffl/season.py`; Next 16 server components fetch it through a `/api/*` rewrite. ESPN is read via authenticated cookies; injuries from ESPN's public feed with a 30-min cache and last-good fallback, so data can be stale and the UI already carries a stale badge.
- **Outlooks:** the paragraph under each Roster card is written by `claude -p` from a fact sheet, cached per week under a hash of the facts, regenerated hourly by the jobs container. GETs never generate. Outlook text never changes the call.
- **Record:** the `decisions` table in Postgres (every roster decision, reasoning, alternatives; `journal/DECISIONS.md` is frozen history, imported there). The app records its own moves (lineup, sit, claims, free adds) and the Decisions page asks for the reason; `uv run python -m ffl.decisions` adds, explains and lists the rest. Also the `transactions`, `results` and `draft_picks` tables, and a sync (Refresh).
- **Pages:** Matchup (default, the head-to-head), Roster, League, NFL (every game of the week, live first; a game opens its scorebug, my and my opponent's starters in it, and a play-by-play feed), Players (every player in the league; a Free agents toggle, on by default, is the waiver view), Journal, Draft.

## Capabilities and Constraints

- **Lineup call** (`season.roster_cards`): start = in the optimal lineup by this week's averaged projection; byes and OUT/IR/SUSP at zero. A **close call** flag when the gap to the alternative is under 2 points or the sources disagree by 3+. QUESTIONABLE/DOUBTFUL keep the projection call and say the status is decided by hand on practice-report news.
- **Waiver ranking:** one composite column, `add`, in projected season points gained (formula in the league's strategy, "Waivers" (`uv run python -m ffl.strategy show`)); raw `delta_roster` and `delta_pool` stay visible beside it. No bye term, no contest term yet ("who else wants him" is planned as its own column when manager models land).
- **Terminology in use:** start / bench / close call; `add`, ROS (rest of season), VORP, ADP, spread (source disagreement), replacement level; QB/RB/WR/TE/FLEX/D/ST/K; IR stash; bye.
- **Hard constraints:** the UI reports the tree's call and the reason; new judgment is not encoded as weights. Injury news overrides the tree by hand, so surfacing status and the healthy alternative matters more than a verdict.
- **Stack:** Next 16 + React 19, Tailwind 4, shadcn (base-ui), lucide icons, react-markdown; Geist / Geist Mono; dark mode follows the system (no toggle yet, `.dark` hook kept). pnpm. Vitest for the web, pytest via `uv run` for Python.
- **Undecided:** whether the ESPN matchup/opponent context belongs on the Matchup page (currently a "matchup rank is not a term" stance for the *call*; its place as *context* is open). Whether the Draft page stays after the season.

## Brand Commitments

Product name in the UI: "FFL" (title), team name "My Team". No logo, no imagery,
no voice guide. No visual constraints were volunteered; none are recorded here.

## Evidence on Hand

- Real league data via ESPN and Sleeper APIs (regenerated into gitignored `data/`).
- Real season record in the SQLite DB (`data/ffl.db`: decisions, transactions, weekly results, draft picks; standings read live from ESPN), with the pre-DB prose history in `journal/`.
- Strategy and the formulas that drive every number: the league's strategy (`uv run python -m ffl.strategy show`).
- Absent, and not to be fabricated: other users, testimonials, comparisons to other apps, historical seasons before 2026.

## Product Principles

1. **The call, then the reason, then everything else.** A screen the user opens on a phone before kickoff answers "who starts" before it shows anything that does not change that answer.
2. **Facts from code, judgment from the rules.** The UI shows what fired (gap, spread, status, bye), never a grade that hides its inputs. Numbers that are estimates say so.
3. **Stale is a state, not an error.** Data can be minutes or hours old; every surface says when it was last true and degrades to last-good rather than blank.
4. **Two hands on a laptop, one thumb on a phone.** Both scenes are primary. Dense tables are fine at a desk if the phone view still gets to the decision in one screen.
5. **The record is written once and read for months.** Decisions and results are captured with their reasoning at the moment they are made and must stay legible long after.
