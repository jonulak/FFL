"""strategy preset 1qb-10team v1: 1QB draft rules and the home preset's in-season rules; the public league plays it

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-25 12:30:00.000000

"""
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0020'
down_revision: Union[str, Sequence[str], None] = '0019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

KEY = "1qb-10team"
NAME = "1QB, 10 teams"
# Jonathan (2026-10-25): no caps tighter than the league's own limits, so the suggestion is ESPN's standard limits
CONSTANTS = {"qb_pace": 0.0, "suggested_caps": {"QB": 4, "RB": 8, "WR": 8, "TE": 3, "K": 3, "D/ST": 3}}
PUBLIC = "espn:950458410"  # Indianapolis Pro H2H Points PPR League, drafted 2026-10-25; linked only where registered

_strategies = sa.table('strategies', sa.column('key', sa.String), sa.column('version', sa.Integer),
                       sa.column('name', sa.String), sa.column('prose', sa.String), sa.column('constants', sa.JSON),
                       sa.column('added_at', sa.String))


def upgrade() -> None:
    """Upgrade schema."""
    op.bulk_insert(_strategies, [{"key": KEY, "version": 1, "name": NAME, "prose": PROSE, "constants": CONSTANTS,
                                  "added_at": datetime.now().astimezone().isoformat(timespec="seconds")}])
    op.execute(sa.text("UPDATE leagues SET strategy = :k WHERE key = :lg AND strategy IS NULL")
               .bindparams(k=KEY, lg=PUBLIC))


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(sa.text("UPDATE leagues SET strategy = NULL WHERE strategy = :k").bindparams(k=KEY))
    op.execute(sa.text("DELETE FROM strategies WHERE key = :k").bindparams(k=KEY))


PROSE = r'''# Draft strategy — 1QB, 10 teams, ESPN standard (full PPR)

**Lineup:** QB, RB, RB, WR, WR, TE, FLEX (RB/WR/TE), D/ST, K · 7 bench · 1 IR.
**Scoring:** full PPR · 4 pt pass TD · 0.04/pass yd · -2 INT · 6 pt rush/rec TD · 0.1/yd.
**Caps:** the league's own position limits (QB 4, RB 8, WR 8, TE 3, K 3, D/ST 3); no tighter caps of mine.

Sources: the projection consensus scored to the league (ESPN, Sleeper; in a mid-season draft the rest-of-season
consensus of Sleeper, CBS and FantasySharks) and FantasyFootballCalculator's 1QB ADP for the league's size and PPR.

## How the call gets made

The watcher's table is a candidate table, not a verdict: `score` orders it, and the call is made on the columns
beside it (`survive_prob`, `urgency`, `need`, `bye_risk`, `spread`, `injury`) against the rules below. Three
options are given one pick before the turn, after the rollout line for that pick has printed.

## Draft rules (1QB)

1. **One starting QB, so QBs come late.** Replacement at QB is high in a 1QB league (twelve or more startable
   passers for ten teams), so no QB before round 5 unless one stands a full tier above the rest; take the starter
   when the QB tier you want is about to empty, not before. A backup QB is a bench pick only for the starter's
   bye, in the last rounds, never ahead of a flex-worthy RB/WR.
2. **Rounds 1-4 are RB and WR,** ordered by score. Between two close options, take the position whose tier
   ends sooner (the `best left` line: where the drop from the best to the next is larger).
3. **TE:** an elite TE (a clear tier above the next) is worth a round 3-4 pick; otherwise wait and take the last
   one before the TE drop-off. One TE; a second only as injury cover late.
4. **Flex and bench:** RB and WR depth by score; bench players rarely start, so prefer upside (a role that grows
   with one injury ahead of him) over a low, steady projection in the second half of the draft.
5. **Injuries are checked, not trusted to the pool.** Every option is checked against ESPN's NFL injuries feed
   before it is named; an OUT or IR player is priced from his return week. A good IR player expected back is
   drafted to the bench in the later rounds and moved to the IR slot after the draft (one slot).
6. **K and D/ST are the last two picks.** Kickers barely separate; in a draft once the season is under way the
   D/ST is chosen on the first scoring week's projection, not the rest of the season.
7. **Byes** are a tiebreak only: avoid three or more starters on one bye week. In a mid-season draft a bye
   already played costs nothing.
8. **Mid-season draft:** projections are rest-of-season from the league's first scoring week; ADP is preseason,
   so `survive_prob` is rough and a player with no ADP reads as sure to survive (he is not). Recent usage (snaps,
   targets) is read beside the table by hand: a riser shows in usage weeks before a projection.

## The rollout (second opinion, `FFL_ROLLOUT=1`)

The Monte Carlo rollout simulates the rest of the draft and scores each candidate by the best lineup it leaves
me. It is a second opinion printed under the table: it breaks a close call (a gap within its standard error)
and is overruled by the rules above. It gives a bench or IR pick almost no value, since those rarely start, so
it is not read for the depth and stash rounds.

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
P = Φ(margin / σ), refitted every Wednesday 07:00 (`launchd/com.jonulak.ffl-winprob`).
'''
