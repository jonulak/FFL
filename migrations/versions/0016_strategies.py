"""strategy presets: STRATEGY.md becomes the first, 2qb-10team v1, and the home league plays it

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-24 21:00:00.000000

"""
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0016'
down_revision: Union[str, Sequence[str], None] = '0015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

KEY = "2qb-10team"
NAME = "2QB, 10 teams"
CONSTANTS = {"qb_pace": 0.0, "suggested_caps": {"QB": 3, "RB": 7, "WR": 7, "TE": 2, "K": 1, "D/ST": 1}}


def upgrade() -> None:
    """Upgrade schema; the preset is not user-editable: a change to it is a new version, added by a migration."""
    strategies = op.create_table('strategies',
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('prose', sa.String(), nullable=False),
        sa.Column('constants', sa.JSON(), nullable=False),
        sa.Column('added_at', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('key', 'version'))
    op.bulk_insert(strategies, [{"key": KEY, "version": 1, "name": NAME, "prose": PROSE, "constants": CONSTANTS,
                                 "added_at": datetime.now().astimezone().isoformat(timespec="seconds")}])
    op.execute(sa.text("UPDATE leagues SET strategy = :k WHERE home").bindparams(k=KEY))


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE leagues SET strategy = NULL")
    op.drop_table('strategies')


# STRATEGY.md as of this revision, verbatim (the file is gone; `uv run python -m ffl.strategy show` prints it)
PROSE = r'''# Draft strategy — "2026 Fantasy Football" (ESPN league 100000001)

**Draft:** Mon 2026-09-07, 7:00 PM EDT · snake · 10 teams · 17 rounds · 90 s/pick · **order drawn 2026-09-07 18:00: slot 5** (picks 5 · 16 · 25 · 36 · 45 · 56 · 65 · 76; order by team id 6,2,3,9,7,8,5,4,1,10). Use the 4–8 row below.
**Lineup:** QB, QB, RB, RB, WR, WR, TE, FLEX, FLEX (RB/WR/TE), D/ST, K · 6 bench · 1 IR.
**Scoring:** full PPR · 4 pt pass TD · 0.04/pass yd · -2 INT · 6 pt rush/rec TD · 0.1/yd.
**Me:** team 7, "My Team". All ten teams are managed by people; nobody autodrafts.

Sources: ESPN + Sleeper projections, both scored to this league and averaged; FantasyFootballCalculator 2QB ADP (10-team, mean and spread); board in `data/board.md`.
Replacement level (the last player who still gets rostered, from the live pool): QB28 ≈ 194, RB38 ≈ 147, WR36 ≈ 186, TE14 ≈ 157.
Re-run `uv run python -m ffl.adp && uv run python -m ffl.players && uv run python -m ffl.sleeper && uv run python -m ffl.board` the morning of the draft.

## How the call gets made

The code computes what nobody can in 90 seconds and prints a **candidate table** (`ffl.draft_watch`, `ffl.advise`):
projection, VORP against the live pool, ADP with the chance each player survives to my *following* pick,
how many teams picking in between still need his position, the cost of waiting at each open slot,
bye clashes, injury status, and how far the two projection sources disagree. Its `score` column is a
default ordering, not the decision. The decision is made against this document, **two picks out**,
as a tree ("if X is gone take Y; if both, Z"), so the on-clock step is matching a branch.

**The rollout makes the tree (decided 2026-09-07, 1:30 PM, after practice draft 10 and the pace tests).**
With `FFL_ROLLOUT=1` a `rollout` line prints under the table (see "The rollout" below). It plays every
candidate out to the end of the draft a few hundred times, with each opponent's picks drawn from ADP
and spread, so "will he still be there at my next pick" is inside its number, not beside it. Left to
draft on its own from every seat it reached two WRs by 65, Bowers/McBride in rounds 4–6, QB3 in the
90–120 window and K/D/ST last, and its rosters beat score's by 20–50 season points at every seat and
every room speed. At the heads-up the tree is **the rollout's top three, with the guards below**:
- If score's leader is within 3 points of the rollout's, the two agree — take whichever the tree already favours.
- If the gap is larger, take the rollout's man. A `ROLLOUT DISAGREES` line is that case with the
  leader clearly outside score's top five; it fired twice in practice draft 10 and was right both times.
- On the clock, the fresh rollout line is a check on the tree, not a new decision, unless a player it
  flags has just fallen. If it disagrees with score by a lot and no guard speaks, read its `then`
  column — what it expects my next pick to be — and ask whether that player will really be there.
- A line that says `failed:` or is missing: use the table, with the guards as the rules.

**Guards — the two things the rollout cannot see, applied by hand:**
1. **QB2 floor.** The rollout maximises the mean season lineup; it does not price the weekly floor or an
   injury behind a bare wire, and on the projections it will let QB2 slide to Willis (250) from any seat
   in any room speed (DECISIONS.md, pace tests). The market prices that risk and the projections do not.
   So: **never let QB2 fall past the top twenty-four (~240, Stroud/D. Jones)**, and when the rollout's
   gap between its leader and the best remaining top-twenty QB is under ten points, take the QB. In
   practice draft 10 this fires at 37 (Purdy/Williams), where rule 1 used to, and nowhere else.
   The old per-slot deadlines are in the table below for reference; they are no longer the rule.
2. **The IR stash.** The rollout has no IR slot. Round 14 at the earliest (Jonathan's call, practice draft 13): a good player on IR (ESPN
   `INJURY_RESERVE`) beats a flier — he is drafted to the bench and moved to IR after the draft, which
   hands the bench spot back. Jordyn Tyson (WR NO, 105 projected by both sources, ADP ~123, bye 8) is
   the one worth it; Pacheco is marginal; Conner/Dell/Kirk are Sleeper-only.

**Retired as rules (the rollout reaches them unaided; kept as things to notice):** two WRs by ~65; one of
Bowers/McBride at a round 4–6 pick, never a third TE (blacklisted in code); QB3 between ~90 and ~120,
never a QB4 (blacklisted); K and D/ST with the last two picks.

**Still by hand — the number, not the rule:** treat the average as unreliable where the sources split:
Jacobs 164/87 (DTD), J. Love 276/212, Hall 274/211, Etienne 247/208, Charbonnet 134/67 (OUT). Decide
those on the morning's news. CMC, Nacua, Chase, Jeanty and Hall carry QUESTIONABLE tags into rounds
1–3 — check status before 7 PM. Neither score nor the rollout sees this; both use the average.

## What the numbers say

1. **The QB pool has a shape, and the shape is the strategy.** 20 QBs start every week. QB1–5 (Allen 361, Lamar 320,
   Maye 315, Hurts 311, Daniels 309), then a plateau QB6–16 from Burrow 300 to Murray 278 (about two points a week
   across eleven quarterbacks), then QB17–20 (Goff 271, Love 266, Mayfield 263, Shough 262), then the cliff: Willis 250,
   Darnold 247, Stroud 241, D. Jones 240, and 231 → 176 after that. Replacement is your own QB3, about QB28 = 194.
   On that baseline Allen is +167 — fourth on the whole board, level with CMC, above Taylor/Nacua/Chase — and the
   tier-2 QBs (+114 to +126) sit level with Cook, Achane, Henry and Barkley. A static baseline cannot see the plateau,
   which is why it is not the lens for rounds 1–3. **The cost of waiting is.**
2. **Cost of waiting by 2QB ADP** (best available at a pick minus best available at the same seat's next pick,
   with every team drafting to ADP):
   - R1→R2: RB −41 to −73 (CMC/Gibbs → Achane), WR −24 to −51 (Nacua/JSN → Lamb), QB −9 from slots 4–10
     (Lamar → Hurts) but −69 from slot 1 (Allen → Dart). **Round 1 is RB/WR from every seat; Allen at 1.01–1.02 is
     the one defensible QB.**
   - The plateau makes waiting on QB cheap for two rounds (−9, then −19 Hurts → Dart, then −14 Dart → Murray), and
     then expensive all at once: Murray (ADP 51) and Love (59) are the last of the top twenty, and after them it is
     Willis. That is why the QB rule is written in pick numbers, not rounds.
3. **Elite RB/WR go first because the drop is the biggest on the board.** Gibbs 349, Bijan 339, CMC 316 (Q), Taylor 294;
   Nacua 333 (Q), Chase 324 (Q), JSN 306, ARSB 302. RB tier 4 (Achane 276 → Irving 202) is wide and flat; WR tier 4
   (London 260 → McLaurin 216) is wider. So RB before WR early, and both before the second QB.
4. **TE is a two-man tier.** Bowers 247 / McBride 238, then Loveland 211 / Warren 206, then LaPorta 193 / Fannin 185.
   Their 2QB ADP is 60–65; the table lifts them by 36 once the TE run starts, and in the simulator one of them goes to
   us between picks 38 and 57 from every slot. Do not take a second TE before round 8, and never a third.
5. **QB3 is real insurance here** (20 start, the wire is bare): Mayfield 43, Shough 50, Love 59, Darnold 60, Willis 85,
   D. Jones 74 by ADP. Take him in the 90–120 window; a QB4 is blacklisted.
   *Observed in both 2026-09-07 practice drafts (vs ESPN bots, so indicative only): the last of the top twenty QBs
   went at 87 and at 93 — i.e. before the window opens, leaving Darnold/Willis/Stroud class at 90–120. If the room
   is running that pace and Love/Mayfield/Goff are the last two on the board, taking QB3 in the 80s costs ~10 points
   of bench RB and saves ~20 at QB. Read `opp_demand` on QB at your turn: 6 QB-hungry picks before your next one is
   the tell.*
6. **K and D/ST are the last two picks.** Aubrey leads K by 8; the best D/ST beats the tenth by 23 points a season.

## By pick number (adjust once the slot is drawn)

| Slot | Picks (R1–R6) | Plan |
|---|---|---|
| 1–3 | 1–3 · 18–23 turn · 38–43 · 58–63 | Elite RB. Turn = WR (Lamb/Jefferson) + one of RB tier 4 / **QB1** (Dart, Purdy, Nix ~292). **QB2 at 38–43** (Murray/Mayfield class) — at 58–63 it is Love/Willis, the cliff. |
| 4–8 | 4–8 · 13–17 · 24–28 · 33–37 · 44–48 · 53–57 | CMC/Taylor/Nacua/Chase/JSN. 13–17: tier-2 QB (Hurts/Daniels) is a coin flip with Achane/Cook — take whichever is last of his tier. **QB1 by 24–28, QB2 by 33–37**; Murray at 44–48 is the fallback, not the plan. Bowers/McBride at 44–57 if there. |
| 9–10 | 9–12 turn · 29–32 · 49–52 · 69–72 | Turn = RB + WR (ARSB/JSN → Collins is −47 if you wait, the biggest drop anywhere); Hurts/Daniels at 11–13 only if the WR is gone. **QB1 at 29–32 (Dart/Nix), QB2 at 49–52 (Murray/Love)** — that is on the cliff, so if Love is the only one left at 49, take him first. |

Targets by tier, for the tree:
- QB tier 2: Lamar, Maye, Hurts, Daniels · plateau: Burrow, Dart, Purdy, Lawrence, Nix, Prescott, C. Williams, Herbert, Mahomes (Q), Stafford, Murray · QB17–20: Goff, Love, Mayfield, Shough.
- RB tier 4: Achane, Cook, Henry, Barkley, C. Brown, Walker, Jeanty (Q), Hampton, J. Love (Q, split), Javonte, Hall (Q, split), Kyren, Etienne (split), Skattebo, Judkins, Swift, Montgomery, Irving.
- WR tier 3/4: Lamb, Jefferson, London, Collins, A.J. Brown, Rice, Pickens, Olave, Nabers (Q), Flowers, G. Wilson, D. Smith, McMillan, Egbuka, Higgins, McConkey, McLaurin.
- Rounds 11–14: upside bench — Montgomery, Henderson, Price, J. Warren, Dowdle, Burden, Sutton, Tate, Fannin/Kittle only if TE-light. Handcuff your own RB1 only. Tyson (IR) in 12–14.

## Bye-week traps
- Week 7: Allen, Daniels, Lawrence, Herbert, Cook, Hampton, McConkey. Don't pair two QBs on bye 7.
- Week 6: Burrow, Murray, Goff, Gibbs, ARSB, Chase, Achane, C. Brown, Jefferson.
- Week 11: Maye, Stafford, Nacua, Bijan, JSN, London, Kyren.
- Week 13: Lamar, Taylor, Jeanty, Henry, Hall, Bowers, G. Wilson.
The table prices a second starter sharing a bye (`bye_risk`); it does not price a whole roster on bye 6.

## Pick numbers by slot (snake, 10 teams)

| Slot | R1 | R2 | R3 | R4 | R5 | R6 | R7 | R8 |
|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 20 | 21 | 40 | 41 | 60 | 61 | 80 |
| 2 | 2 | 19 | 22 | 39 | 42 | 59 | 62 | 79 |
| 3 | 3 | 18 | 23 | 38 | 43 | 58 | 63 | 78 |
| 4 | 4 | 17 | 24 | 37 | 44 | 57 | 64 | 77 |
| 5 | 5 | 16 | 25 | 36 | 45 | 56 | 65 | 76 |
| 6 | 6 | 15 | 26 | 35 | 46 | 55 | 66 | 75 |
| 7 | 7 | 14 | 27 | 34 | 47 | 54 | 67 | 74 |
| 8 | 8 | 13 | 28 | 33 | 48 | 53 | 68 | 73 |
| 9 | 9 | 12 | 29 | 32 | 49 | 52 | 69 | 72 |
| 10 | 10 | 11 | 30 | 31 | 50 | 51 | 70 | 71 |

## The rollout (second opinion, `FFL_ROLLOUT=1`)

Added the afternoon of the draft (`ffl/rollout.py`). For each candidate it plays the rest of the draft out
a few hundred times — opponents drafting to 2QB ADP with each player's own spread, constrained by what their
rosters can still use (no QB4, no TE3, K/D/ST in the last three picks, empty starters filled when picks run
out); my own later picks planned each time as an assignment of my open starting slots to my remaining picks
on the expected best available at each — and scores the candidate by the season points of my best legal
lineup, re-picked every week with bye players out. The simulations are paired (same opponents, same noise,
different first move), so the gap to the leader is precise to a point or two even at a hundred runs.

It is printed under the table, not in it, and it does not enforce the hard rules above. Read it as the
answer to "what roster does this pick lead to, on the projections and the ADP feed", against the table's
"what is he worth now and what does waiting cost". What it says on today's numbers, from the simulator:
- The elite tier is flat. From most seats Gibbs, Bijan, Allen and Nacua sit within ten points of each
  other over a whole draft; CMC and Taylor are fifteen to thirty behind. Take the tier, not the name.
- It leans WR earlier than the table does (the table's RB lean is partly ESPN's RB optimism, see
  DECISIONS.md), and it prices the QB2 slot as no steeper than WR — it will show a WR or RB over Murray
  at 44 and let QB2 slide to the cliff, at any room speed (the pace tests in DECISIONS.md: a faster room
  moves its QB1 earlier, not its QB2). That is a verdict on the projections, not on the opponent model,
  and it is what the QB2 floor guard above is for.
- A `ROLLOUT DISAGREES` line means its leader is outside the score top five: the slid elite player or the
  position about to run. That is the line worth thirty seconds.

## Draft-day checklist
1. Morning: `uv run python -m ffl.adp && uv run python -m ffl.players && uv run python -m ffl.sleeper && uv run python -m ffl.board`
   (injury news + ADP shift), then `uv run pytest tests/ -q`.
2. 6:30 PM: `uv run python -m ffl.browser` → log in to ESPN in that Chrome window once → it will open the draft room.
3. **When the room shows the draft order:** `uv run python -m ffl.settings` (or `export FFL_PICK_ORDER=<team ids in order>`),
   then `uv run python -m ffl.simulate <slot> --rounds 6` to see the plan from that seat.
4. `FFL_ROLLOUT=1 uv run python -m ffl.draft_watch` in another terminal **before pick 1**; confirm the banner shows
   the drawn slot. Claude reads the candidate table and the rollout line at every pick and gives the decision tree
   at two picks out: the rollout's top three, with the two guards ("The rollout makes the tree").
5. After the draft: move any IR pick to the IR slot; fill `journal/draft/recap.md`; record reasoning for each pick with
   `uv run python -m ffl.decisions add`; commit, push.

## Waivers (in-season, 2026-09-08)

The UI's `add` column is the one composite number in the season tooling. It is points, so it reads
"projected points I gain this season by adding him":

```
add = (ros_delta + week_delta if he would start) × injury_factor
ros_delta   = ROS − max(my weakest rostered at his position, the pool's replacement at his position)
week_delta  = this week's optimal lineup with him − without him (0 if he would not start, and 0 once
              the week's games are all played: there is nothing left to gain this week)
injury      = QUESTIONABLE 0.85 · DOUBTFUL 0.7 · OUT/IR/SUSP: no week term; when ROS fell back to the season formula it counts only the games from the reported return week — the haircut is on ROS itself, before the deltas, never on a delta (which would pull a negative one toward zero and float a season-ending IR player to the top of the table) (ESPN's public injuries feed; OUT 1 / IR·SUSP 4 games when no date is published or the date has passed) — the ROS sources already leave out the games he misses (Sleeper's and FantasySharks' weekly sums have no row for a week out; CBS counts games left), so their number is not scaled again
ROS         = the usage-adjusted rest-of-season projection (below); `ros_proj` is the sources' consensus it starts from
ROS model   = the consensus (Hodges-Lehmann: median of the values and their pairwise means; the mean with two) of Sleeper's weekly projections summed over weeks w..18, CBS's rest-of-season projections and FantasySharks' weekly projections summed over weeks w..18 (like Sleeper's), all rescored; season projection × (18 − w) / 17 only when none has him
w           = the ROS week: the first week whose games are not all played (every kickoff in it more than
              four hours ago, off ESPN's pro schedule) — not ESPN's scoring period, which rolls some time
              Tuesday and until then has the sum counting a game already played
```

**Usage-adjusted ROS (2026-09-22).** The ROS sources sit on their preseason prior and move a few points a
week, so after two games the column still mostly said what it said in August. The ROS that `add` and
the deltas run on is the model's per-game rate shrunk toward his recent games, with the prior worth
`K` games and each game in the window worth its snap share against the position's lead players:

```
games_left  = games from w to 18, his bye (if still ahead) taken out
ros_pg      = ROS model ÷ games_left
ref_pos     = median snap share that week of the league's lead players at his position
              (top 32 RB, 64 WR, 32 TE, 32 QB by snaps — one, two, one and one per team)
weight_g    = min(1, snap% ÷ ref_pos) for a game with snaps; 1 for a game without them yet, for a K
              or a D/ST, or when no reference could be built for that week
n_eff       = Σ weight_g over the window
ROS         = games_left × (n_eff × l3 + K × ros_pg) ÷ (n_eff + K)         K = 4
```

`K = 4` is a guess about noise, not a fit: a game's scatter is about as wide as the player's average
and the prior misses the true rate by a third or so, which makes the prior worth four to eight games;
the low end because the prior is known to drag. A full three-game window at a starter's snap share is
43% of the number, one game 20%, a fill-in on 40% of the snaps 9%. It is the one constant, checked
rather than trusted: every pool build writes the DB's `calibration_rows` (model ROS, l3, n_eff
and games_left per player) once per ROS week, and `uv run python -m ffl.calibrate` scores each
snapshot against what the players then did, per K. Change K here when that says so, not when a table
looks wrong. This is a deliberate exception to "read form beside add, never inside it": the form
columns are still shown raw, and the blend is one named constant, not a weight per story.

Two facts sit beside the number for the stories the blend cannot price: `exp` (years in the league,
R for a rookie, from Sleeper) and `ahead` (teammates at his position on ESPN's injury report, with
status and return week) — a rookie breaking out and a veteran filling in for a two-week hamstring
look the same in three games, and the call between them is made by hand.

ROS is three in-season models: Sleeper (every row is Rotowire's, `company: rotowire`), CBS and
FantasySharks, each its own. All are revised in season. Sleeper's and FantasySharks' weekly projections, summed over w..18,
have no row for a bye or a week out; CBS's `gp` column counts only the games left. The season totals they replaced (2026-09-17) were frozen at draft
day. CBS lists the top 100 at a position, so deep free agents have fewer sources, and K and D/ST are
Sleeper-only throughout: CBS scores kickers a flat 3 a field goal and its defence points run about 2.7
a game above Sleeper's standard points, so neither is the league's number. The robust average shrugs
off one source with a stale injury or a wild number; the Players page shows the spread (the sources' trimmed range) beside ROS, and
says so in the header when it had to fall back to season totals for the whole pool. Each source's
number is recorded per player until kickoff, and `ffl.calibrate sources` fits per-source weights
Wednesdays against what happened; the weights are stored inactive and apply only once activated by hand.

No bye term (ROS already prices the bye week) and no contest term (who else wants him is a
different unit; it gets its own column when the manager models land). Raw `delta_roster` and
`delta_pool` stay visible beside `add`. Change this section first, then `ffl/season.py`.

**Recent form (2026-09-21).** The ROS sources are anchored to their preseason priors and move a few
points a week; a breakout shows in usage two or three weeks before it shows in a projection. So the
Waivers table and the Roster cards carry the last **three games he played** (within the last five
weeks, which bounds the ESPN calls). They are shown raw; the only one of them inside `add` is the
snap-weighted `l3` through the usage-adjusted ROS above:

```
window    = his last 3 games with an opportunity (carry, target or pass attempt; FG or XP attempt
            for a K; any stat line for a D/ST) and a final score — byes, OUT/IR weeks and the weeks
            before a return simply are not games; fewer than 3 shows the count, none shows nothing
l3        = points per game over the window (ESPN's scoring, i.e. the box score)
beat      = l3 − ESPN's weekly projection over the same games (Sleeper's past weeks are not kept)
opp       = touches + targets per game (pass attempts for a QB; FG + XP attempts for a K)
opp_trend = latest game's opportunity − the mean of the earlier games in the window
snap%     = offensive snaps ÷ team offensive snaps over the games Sleeper has snaps for (they land
            a day or so after the games); QB/RB/WR/TE only
tgt/snap  = targets ÷ offensive snaps over those games (RB/WR/TE); the stand-in for a route rate
ypt       = receiving yards ÷ targets over the window (RB/WR/TE)
air%      = his completed air yards ÷ his club's (its passers') over the games Sleeper's row has landed
            for, with the snaps (RB/WR/TE); a catch behind the line counts negative
air/c     = completed air yards ÷ catches over those games (RB/WR/TE); a QB's ÷ his completions
rz        = red-zone targets (RB/WR/TE), rushes (QB/RB) and pass attempts (QB), per game over them
pace_gap  = l3 × games left (his bye, if still ahead, taken out) − ROS model: "if the last three held"
weight    = Σ weight_g (above): how many starter-share games the window is worth to the blend
```

Yards per route run needs routes run, which is charting data (PFF, Fantasy Points, ESPN Analytics,
all paid; nflverse stopped publishing participation after 2023), so it is not here; `tgt/snap` and
`ypt` are the nearest free numbers. Intended air yards (aDOT, the WOPR air share) are charting data
too: Sleeper's `rec_air_yd` / `pass_air_yd` are *completed* air yards (never more than the yards; a
deep incompletion counts nothing), so `air%` and `air/c` say where he was caught, not where he was
thrown to. Sort order stays `add`. Change this section first, then
`ffl/season.py::recent_form`.

## Lineup (in-season, 2026-09-08)

**Projection sources (2026-10-11).** The weekly number is the consensus of ESPN, Sleeper, CBS,
FantasySharks and FleaFlicker (ROS: Sleeper, CBS, FantasySharks), a Hodges-Lehmann robust average:
FantasyFootballAnalytics' method (robust average over many sites). Its own API is paid and its R
scrapers break, so the sites are read directly. NFL.com's projections are gone; FantasyPros is a
consensus already; FFToday and numberFire are left out (down / JavaScript).

Start = in the optimal lineup by this week's projection (the consensus of ESPN, Sleeper, CBS, FantasySharks and FleaFlicker, byes and
OUT/IR/SUSP at zero). The Roster page flags a **close call** when the gap between a player and
the alternative for his slot is under 2 points (`CLOSE_GAP`), or the sources disagree by 3
or more (`CLOSE_SPREAD`: the consensus spread, the range across sources with the single highest and
lowest dropped at four or more, |a − b| with two). QUESTIONABLE and DOUBTFUL keep their projection-based call; the status
is decided by hand on practice-report news, and the card's reason line says so. Matchup rank is
not a term: the sources already price the opponent. The outlook text under each card is written
by Claude from the card's facts alone and never changes the call. Change this section first, then
`ffl/season.py`.

**Slot by kickoff (2026-10-07).** The call names the slot, not just start/sit: QB, RB, WR, TE, FLEX,
D/ST, K, or BENCH. Projection picks *who* starts, as above; the slot each starter takes is decided by
kickoff. Within each position, the earliest games take the position's own slots and the latest take the
flex spots, so a late scratch (inactives come out 90 minutes before kickoff) sits in FLEX, where anyone
still unplayed at RB, WR or TE can replace him. Who starts and the projected total never change; only
the placement does, since the number of each position in flex is fixed by who starts. A starter whose
game has started keeps the slot ESPN has him in (he cannot move). The chip is ringed when ESPN has him
in another slot. Nor can a started game be called around (2026-10-11): a starter whose game has started
starts whatever his projection, a bench player whose game has started is BENCH, and neither is anyone's
alternative in a card's gap. The call is the best lineup still reachable.

**Win probability (2026-10-05)** on the Matchup scorebug and League tiles is context, not a rule: no
start/sit call reads it. Each side is posted points plus every playable starter's projection times the
share of his game left, with a per-position spread (σ) fitted by `uv run python -m ffl.calibrate winprob`;
P = Φ(margin / σ), refitted every Wednesday 07:00 (`launchd/com.jonulak.ffl-winprob`). Weeks 1-3 fit: σ QB 8.6, RB 8.2, WR 8.5, TE 6.9, D/ST 5.8, K 4.2; the pregame odds
scored Brier 0.203 over 15 games (a coin flip is 0.25), in-sample.

**Props trial (weeks 4-6, 2026-09-30).** Betting player props (The Odds API, free tier) are recorded as
a third weekly source but do **not** change the call yet. A props number is Sleeper's stat line with
each priced stat replaced by the books' consensus (median yardage/reception lines; TDs as a Poisson
mean off the devigged price) and rescored to the league. Snapshots Thursday before the night game
and Sunday 11:30 ET; each game is judged off the last snapshot before its kickoff (`ffl/props.py`).
The bar, fixed before any result: over every rostered QB/RB/WR/TE with a line, the ESPN + Sleeper +
props mean must have a **lower pooled MAE than ESPN + Sleeper and be better in at least two of the
three weeks**. Pass: props join the mean here (as a third source, no separate weight), then in
`ffl/season.py`. Fail: the call stays as it is and the trial ends. Why a trial and not a switch: the
only published evidence is one vendor study showing a ~1.5% MAE edge, and both projections already
read the game lines; the case for props is freshness (Thursday and Sunday news) and a market-calibrated
per-player number. `uv run python -m ffl.props score` also prints the lineup swaps props would have made.
The trial keeps its ESPN + Sleeper baseline (`ffl/props.py` `baseline`) though the board's weekly number
became the five-source consensus on 2026-10-11; on a pass, props join that consensus as one more source.
**Verdict (2026-10-21): fails; the trial is over.** MAE ESPN + Sleeper vs + props: week 4 6.34 vs 6.33
(132 players), week 5 6.58 vs 6.61 (125), week 6 5.58 vs 5.56 (106); pooled 6.20 vs 6.20 (6.2007 vs 6.2016
unrounded), props better in two weeks by 0.01-0.02. Not lower pooled, so the bar is missed; props would have
swapped none of my starters in any week. The pull agent is gone; `ffl/props.py` and the raw responses under
`data/props/` stay, so a different conversion can still be scored without spending credits.
'''
