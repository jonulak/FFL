"""The season's shared state in one SQL database (SQLAlchemy Core; Alembic owns the schema).

The only module that talks SQL. Every function takes and returns plain dicts, so season.py stays
pure. SQLite in WAL mode by default (`<DATA>/ffl.db`); FFL_DB_URL points anywhere else (tests, a
later Postgres). Writes are one short transaction each, BEGIN IMMEDIATE on SQLite so a second
writer waits on busy_timeout instead of failing on a lock upgrade.

    uv run alembic upgrade head          # create / migrate the schema
    uv run python -m ffl.db import       # one-time: the old journal/ and data/ files in (idempotent)
    uv run python -m ffl.db backup       # a checked snapshot into the backup dir (launchd nightly)
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager

import sqlalchemy as sa
from sqlalchemy import event

from .config import DATA, ROOT, SEASON

metadata = sa.MetaData()


class SchemaError(RuntimeError):
    pass


DBError = sa.exc.SQLAlchemyError  # callers outside db.py catch DB errors through this


def _season():
    return sa.Column("season", sa.Integer, nullable=False, default=SEASON)


def _league():
    """The league a row belongs to, a registry key ("espn:100000001"); the phase-1b tables carry it."""
    return sa.Column("league", sa.String, nullable=False)


leagues_table = sa.Table(
    "leagues", metadata,
    sa.Column("key", sa.String, primary_key=True),       # "<provider>:<provider league id>"
    sa.Column("provider", sa.String, nullable=False),
    sa.Column("provider_id", sa.String, nullable=False),
    sa.Column("season", sa.Integer, nullable=False),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("team_id", sa.Integer),                    # mine in that league; NULL until known
    sa.Column("home", sa.Boolean, nullable=False, default=False),
    sa.Column("lineup_format", sa.String),
    sa.Column("strategy", sa.String),                    # strategies.key; the league plays its latest version
    sa.Column("added_at", sa.String, nullable=False),
)

# Strategy presets (ffl/strategy.py): shared, and not user-editable; only migrations add a key or a version.
strategies = sa.Table(
    "strategies", metadata,
    sa.Column("key", sa.String, nullable=False),         # "2qb-10team"
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("prose", sa.String, nullable=False),       # markdown, what Claude reads while advising
    sa.Column("constants", sa.JSON, nullable=False),     # {"qb_pace": float, "suggested_caps": {pos: n}}
    sa.Column("added_at", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("key", "version"),
)

# Formats are shared: one row per distinct content (ffl/formats.py), linked per league.
scoring_formats = sa.Table(
    "scoring_formats", metadata,
    sa.Column("key", sa.String, primary_key=True),       # "sc-<pos>-<sha1[:12]>" of the table
    sa.Column("pos", sa.String, nullable=False),
    sa.Column("rescore_key", sa.String, nullable=False),  # the same, over formats.RESCORED only
    sa.Column("table", sa.JSON, nullable=False),         # {neutral stat: points}
    sa.Column("added_at", sa.String, nullable=False),
)

league_scoring = sa.Table(
    "league_scoring", metadata,
    _league(),
    sa.Column("pos", sa.String, nullable=False),
    sa.Column("scoring_format", sa.String, nullable=False),
    sa.Column("unmapped", sa.JSON, nullable=False),      # ESPN abbrs that score but no format can place
    sa.PrimaryKeyConstraint("league", "pos"),
)

lineup_formats = sa.Table(
    "lineup_formats", metadata,
    sa.Column("key", sa.String, primary_key=True),       # "ln-<sha1[:12]>"
    sa.Column("doc", sa.JSON, nullable=False),           # formats.lineup_from_settings
    sa.Column("added_at", sa.String, nullable=False),
)

league_settings_table = sa.Table(
    "league_settings", metadata,
    _league(),
    sa.Column("season", sa.Integer, nullable=False),
    sa.Column("doc", sa.JSON, nullable=False),           # everything else ffl.settings.fetch read (formats.rows_for)
    sa.Column("fetched_at", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("league", "season"),
)

league_caps = sa.Table(
    "league_caps", metadata,
    _league(),
    sa.Column("pos", sa.String, nullable=False),
    sa.Column("provider_limit", sa.Integer),             # the league's own cap (ESPN positionLimits); NULL: none
    sa.Column("my_cap", sa.Integer),                     # mine, never above the league's; NULL: none
    sa.PrimaryKeyConstraint("league", "pos"),
)

# Draft inputs (ffl/players.py, ffl/sleeper.py, ffl/adp.py): fetched once, shared by every league they fit.
espn_pools = sa.Table(
    "espn_pools", metadata,
    sa.Column("scoring_format", sa.String, nullable=False),  # a position's scoring_formats key: ESPN scores on it
    sa.Column("season", sa.Integer, nullable=False),
    sa.Column("rows", sa.JSON, nullable=False),          # that position's players, espn_players.csv's columns less ownership
    sa.Column("fetched_at", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("scoring_format", "season"),
)

sleeper_season = sa.Table(
    "sleeper_season", metadata,
    sa.Column("season", sa.Integer, primary_key=True, autoincrement=False),
    sa.Column("raw", sa.JSON, nullable=False),           # Sleeper's season projections as it answered: rescored on read
    sa.Column("fetched_at", sa.String, nullable=False),
)

adp_feeds = sa.Table(
    "adp_feeds", metadata,
    sa.Column("fmt", sa.String, nullable=False),         # FFC's format: 2qb, ppr, half-ppr, standard
    sa.Column("teams", sa.Integer, nullable=False),
    sa.Column("year", sa.Integer, nullable=False),
    sa.Column("rows", sa.JSON, nullable=False),          # adp.fetch's frame, adp_2qb.csv's columns
    sa.Column("meta", sa.JSON, nullable=False),          # FFC's meta (drafts counted, date range)
    sa.Column("fetched_at", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("fmt", "teams", "year"),
)

transactions_table = sa.Table(  # not `transactions`: that name is the reader below
    "transactions", metadata, _league(), _season(),
    sa.Column("id", sa.String, nullable=False),          # `{epoch ms}-{team_id}-{action}-{player_id}`, as journal.py keys it
    sa.Column("date", sa.String, nullable=False),        # ISO minutes, local
    sa.Column("type", sa.String, nullable=False),
    sa.Column("team", sa.String, nullable=False),
    sa.Column("player", sa.String, nullable=False),
    sa.Column("player_id", sa.Integer),                  # parsed from id; None if malformed
    sa.Column("action", sa.String, nullable=False),
    sa.Column("bid", sa.Integer, nullable=False, default=0),
    sa.Column("reasoning", sa.String, nullable=False, default=""),
    sa.PrimaryKeyConstraint("league", "season", "id"),
)
sa.Index("ix_transactions_player", transactions_table.c.league, transactions_table.c.season,
         transactions_table.c.player_id)

draft_picks = sa.Table(
    "draft_picks", metadata, _league(), _season(),
    sa.Column("overall", sa.Integer, nullable=False),
    sa.Column("round", sa.Integer, nullable=False),
    sa.Column("pick", sa.Integer, nullable=False),
    sa.Column("team_id", sa.Integer, nullable=False),
    sa.Column("team", sa.String, nullable=False, default=""),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("player", sa.String, nullable=False, default=""),
    sa.Column("pos", sa.String),
    sa.Column("nfl", sa.String),
    sa.Column("adp_2qb", sa.Float),
    sa.Column("board_rank_at_pick", sa.Float),
    sa.Column("proj", sa.Float),
    sa.Column("ts", sa.String),
    sa.PrimaryKeyConstraint("league", "season", "overall"),
)

# The live draft (ffl/draft_socket.py, ffl/draft_watch.py): the room's raw pick feed, and the watcher's state.
draft_feeds = sa.Table(
    "draft_feeds", metadata, _league(), _season(),
    sa.Column("picks", sa.JSON, nullable=False),         # [{teamId, playerId, lineupSlotId, overall}] in arrival order
    sa.Column("offset", sa.Integer, nullable=False),     # picks made before the feed attached, still unaccounted for
    sa.Column("updated_at", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("league", "season"),
)

draft_states = sa.Table(
    "draft_states", metadata, _league(), _season(),
    sa.Column("doc", sa.JSON, nullable=False),           # the watcher's full state, as each tick left it
    sa.Column("updated_at", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("league", "season"),
)

# Finished drafts kept as replay material (`ffl.replay`): FFC's public mock rooms (`ffl.ffc fetch`) and the practice
# and mock drafts once written to journal/draft/*.csv. Every source's rows are the pick-log shape the watcher writes.
draft_logs = sa.Table(
    "draft_logs", metadata,
    sa.Column("source", sa.String, nullable=False),      # "ffc" | "practice"
    sa.Column("log_id", sa.String, nullable=False),      # FFC's draft id; a practice log's old file stem
    sa.Column("meta", sa.JSON),
    sa.Column("rows", sa.JSON, nullable=False),          # [{overall, round, pick, team_id, player_id, ...}] in pick order
    sa.Column("added_at", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("source", "log_id"),
)

# The on-device trace of the push tap path (POST /trace, ffl-e9x.10). Debug only, never read by the app.
traces = sa.Table(
    "traces", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("at", sa.String, nullable=False),
    sa.Column("doc", sa.JSON, nullable=False),           # {event, **fields}
)

# The props trial (ffl/props.py, weeks 4-6): each pull's snapshot, and The Odds API's raw answers per event.
props_snapshots = sa.Table(
    "props_snapshots", metadata,
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("kind", sa.String, nullable=False),        # "thu" | "sun"
    sa.Column("doc", sa.JSON, nullable=False),           # {week, kind, taken_at, events, credits_left, rows}
    sa.PrimaryKeyConstraint("week", "kind"),
)

props_raw = sa.Table(
    "props_raw", metadata,
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("event_id", sa.String, nullable=False),
    sa.Column("doc", sa.JSON, nullable=False),
    sa.PrimaryKeyConstraint("week", "kind", "event_id"),
)

results = sa.Table(
    "results", metadata, _league(), _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("opponent", sa.String, nullable=False),
    sa.Column("my_score", sa.Float, nullable=False),
    sa.Column("opp_score", sa.Float, nullable=False),
    sa.Column("result", sa.String(1), nullable=False),
    sa.PrimaryKeyConstraint("league", "season", "week"),
)

plays = sa.Table(
    "plays", metadata, _league(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("at", sa.String, nullable=False),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("team_id", sa.Integer),
    sa.Column("total", sa.Float, nullable=False),
    sa.Column("body", sa.JSON, nullable=False),          # the whole row as plays.events_from made it
    sa.UniqueConstraint("league", "season", "week", "player_id", "at", "total", name="uq_plays_event"),
)
sa.Index("ix_plays_week_team", plays.c.league, plays.c.season, plays.c.week, plays.c.team_id)

# Every NFL play, once (ffl/playbook.py): FastCast's as the live feed sees them, ESPN's core drives' (source "espn")
# over them once read. A feed row links to its play by play_id and shows this text, so a play's later rewording
# ("No Play", a review) reaches every row on it.
nfl_plays = sa.Table(
    "nfl_plays", metadata,
    _season(),
    sa.Column("play_id", sa.String, nullable=False),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("game_id", sa.String, nullable=False),
    sa.Column("source", sa.String, nullable=False),     # live | espn
    sa.Column("body", sa.JSON, nullable=False),         # the play as playbook.from_fastcast / from_drives made it
    sa.PrimaryKeyConstraint("season", "play_id"),
)
sa.Index("ix_nfl_plays_week", nfl_plays.c.season, nfl_plays.c.week)

# A play called back that cost a starter points (playbook.callbacks): one row per (play, player), rewritten whenever
# the play or his feed rows on it change, gone when it no longer reads called back.
called_back = sa.Table(
    "called_back", metadata,
    _season(),
    sa.Column("play_id", sa.String, nullable=False),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("body", sa.JSON, nullable=False),         # a play-feed row, match "called_back"
    sa.PrimaryKeyConstraint("season", "play_id", "player_id"),
)
sa.Index("ix_called_back_week", called_back.c.season, called_back.c.week)

# Every week's box scores as ESPN answered them (espn_season.box_scores): the matchup schedule and the two maps
# BoxScore takes, so a cold process parses them back without ESPN. fetched_at (epoch s) moves on every read; doc and
# hash only when ESPN's answer changed. settled: the correction pass's last read (ffl-7sq); never fetched after it.
# final: read after the week finished (week < ESPN's), so a baseline; a current-week (maybe mid-game) read is not.
box_scores_table = sa.Table(
    "box_scores", metadata, _league(), _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("doc", sa.JSON, nullable=False),
    sa.Column("hash", sa.String, nullable=False),
    sa.Column("fetched_at", sa.Float, nullable=False),
    sa.Column("settled", sa.Boolean, nullable=False, default=False, server_default=sa.text("0")),
    sa.Column("final", sa.Boolean, nullable=False, default=False, server_default=sa.text("0")),
    sa.PrimaryKeyConstraint("league", "season", "week"),
)

# A rostered player's game rows for an earlier week he sat on no fantasy roster (espn_season._gap_games).
gap_games_table = sa.Table(
    "gap_games", metadata, _league(), _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("rows", sa.JSON, nullable=False),
    sa.Column("fetched_at", sa.Float, nullable=False),
    sa.PrimaryKeyConstraint("league", "season", "week", "player_id"),
)

# A finished week's points that changed between two reads (ESPN's stat corrections). team_id NULL: a gap-week row.
box_score_corrections = sa.Table(
    "box_score_corrections", metadata, _league(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("team_id", sa.Integer),
    sa.Column("old", sa.Float, nullable=False),
    sa.Column("new", sa.Float, nullable=False),
    sa.Column("at", sa.String, nullable=False),
)

outlooks = sa.Table(
    "outlooks", metadata, _league(), _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("kind", sa.String, nullable=False),        # outlook.ROSTER ("week") / outlook.WAIVERS ("waivers-week")
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("entry", sa.JSON, nullable=False),         # hash, card_hash, bullets, generated_at, error
    sa.Column("updated", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("league", "season", "week", "kind", "player_id"),
)

calibration_snapshots = sa.Table(
    "calibration_snapshots", metadata, _league(), _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("k", sa.Float, nullable=False),
    sa.Column("taken_at", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("league", "season", "week"),
)

calibration_rows = sa.Table(
    "calibration_rows", metadata, _league(), _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("pos", sa.String, nullable=False),
    sa.Column("model", sa.Float, nullable=False),
    sa.Column("l3", sa.Float),
    sa.Column("weight", sa.Float, nullable=False),
    sa.Column("games_left", sa.Integer, nullable=False),
    sa.PrimaryKeyConstraint("league", "season", "week", "player_id"),
    sa.ForeignKeyConstraint(["league", "season", "week"], ["calibration_snapshots.league", "calibration_snapshots.season",
                                                       "calibration_snapshots.week"]),
)

sigma_fits = sa.Table(
    "sigma_fits", metadata, _season(),
    sa.Column("fitted_at", sa.String, nullable=False),
    sa.Column("fit", sa.JSON, nullable=False),           # {"sigma": {...}, "n": {...}, "weeks": [...]} as write_sigma wrote it
    sa.PrimaryKeyConstraint("season", "fitted_at"),
)

# Each projection source's number per player (the league's strategy, "Lineup" (`uv run python -m ffl.strategy show`) / "Waivers"): what the consensus read,
# kept so `ffl.calibrate sources` can fit per-source weights. kind "week" (that week's game) or "ros" (the
# ROS week's rest of season); written until his kickoff, then left alone (espn_season.record_projections).
projection_rows_table = sa.Table(  # not `projection_rows`: that name is the reader below
    "projection_rows", metadata, _league(), _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("source", sa.String, nullable=False),
    sa.Column("points", sa.Float, nullable=False),
    sa.Column("taken_at", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("league", "season", "week", "kind", "player_id", "source"),
)

# Fitted per-source weights ({"week": {pos: {source: w}}, "ros": {...}, "report": {...}}); none applies
# until one is activated by hand (`ffl.calibrate sources --activate <fitted_at>`).
source_weights = sa.Table(
    "source_weights", metadata, _season(),
    sa.Column("fitted_at", sa.String, nullable=False),
    sa.Column("fit", sa.JSON, nullable=False),
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.PrimaryKeyConstraint("season", "fitted_at"),
)

claim_plans = sa.Table(
    "claim_plans", metadata, _league(), _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("plan", sa.JSON, nullable=False),
    sa.Column("updated", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("league", "season", "week"),
)

# The players sat by hand for a week (the Roster sheet's toggle): left out of the lineup call. A row is a sit and
# the toggle off deletes it, so a new week starts with none.
sits = sa.Table(
    "sits", metadata, _league(), _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("at", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("league", "season", "week", "player_id"),
)

# The players on the watchlist (the Players page's eye toggle). A row is a watch; unwatching deletes it.
watchlist = sa.Table(
    "watchlist", metadata, _league(), _season(),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("added_at", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("league", "season", "player_id"),
)

# What happened to a watched player (kind ahead/usage/news/bench/available), one row per `key`; unwatching keeps them.
# Not `watch_signals`: that name is the reader below.
watch_signals_table = sa.Table(
    "watch_signals", metadata, _league(), _season(),
    sa.Column("key", sa.String, nullable=False),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("text", sa.String, nullable=False),
    sa.Column("at", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("league", "season", "key"),
    sa.Index("ix_watch_signals_player", "league", "season", "player_id"),
)

# Which row of a starting slot each player stands in (season.reseat): ESPN's two WR slots are one slot to it and it
# lists them in roster-acquisition order, so a swap would jump rows. A player stands in `seat_id`'s place in that
# order; one with no row stands in his own.
seats = sa.Table(
    "seats", metadata, _league(), _season(),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("seat_id", sa.Integer, nullable=False),
    sa.PrimaryKeyConstraint("league", "season", "player_id"),
)

# Web Push: one row per installed device (the browser's PushSubscription). A 404/410 from the push service deletes it.
push_subscriptions = sa.Table(
    "push_subscriptions", metadata,
    sa.Column("endpoint", sa.String, primary_key=True),
    sa.Column("keys", sa.JSON, nullable=False),          # {p256dh, auth}
    sa.Column("ua", sa.String, nullable=False, default=""),
    sa.Column("created_at", sa.String, nullable=False),
)

# The notification outbox (ffl/notify.py): every detector inserts if absent on key, so an event is one notice however
# often it is seen, across restarts. sent_at stays NULL until the flush delivers it or marks it off/expired.
notices = sa.Table(
    "notices", metadata, _league(), _season(),
    sa.Column("key", sa.String, nullable=False),         # move:<tx id> / injury:<week>:<pid>:<status> / waivers:<run>
    sa.Column("kind", sa.String, nullable=False),        # move / trade / injury / waivers
    sa.Column("title", sa.String, nullable=False),
    sa.Column("body", sa.String, nullable=False),
    sa.Column("url", sa.String, nullable=False),
    sa.Column("created_at", sa.String, nullable=False),
    sa.Column("sent_at", sa.String),
    sa.Column("outcome", sa.String),                     # sent / off / expired
    sa.PrimaryKeyConstraint("league", "season", "key"),
)
sa.Index("ix_notices_unsent", notices.c.league, notices.c.season, notices.c.sent_at)

# notify's GLOBAL prefs and memory, one JSON value per key; a league's own keys (LEAGUE_STATE_KEYS) live in league_state.
notify_state = sa.Table(
    "notify_state", metadata, _season(),
    sa.Column("key", sa.String, nullable=False),
    sa.Column("value", sa.JSON, nullable=False),
    sa.PrimaryKeyConstraint("season", "key"),
)

# The state keys that belong to one league (what a waiver run or a claim did is that league's), kept in league_state.
LEAGUE_STATE_KEYS = frozenset({"waiver_run", "pending_claims", "watch_state", "player_presets", "pregame_pulls",
                               "decision_prefs", "final_plays_done", "news_to_notify"})
league_state = sa.Table(
    "league_state", metadata, _league(), _season(),
    sa.Column("key", sa.String, nullable=False),
    sa.Column("value", sa.JSON, nullable=False),
    sa.PrimaryKeyConstraint("league", "season", "key"),
)

# The decision journal (was journal/DECISIONS.md): every lineup move, sit, claims submit and free add the app makes,
# and what sessions record through `python -m ffl.decisions`. rationale NULL = still needs one (the Decisions page
# lists it as open); a list of preset keys (ffl.decisions.PRESETS) otherwise. import_key is set only on rows the
# one-time import made from the markdown file (`<heading>#<n>`), so re-running it inserts nothing.
decisions_table = sa.Table(
    "decisions", metadata, _league(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    _season(),
    sa.Column("at", sa.String, nullable=False),          # ISO seconds with offset (app/session); YYYY-MM-DD (import)
    sa.Column("week", sa.Integer),
    sa.Column("kind", sa.String, nullable=False),        # lineup|sit|unsit|claims|free_add|trade|draft|note
    sa.Column("source", sa.String, nullable=False),      # app|session|import
    sa.Column("title", sa.String, nullable=False),
    sa.Column("decision", sa.String, nullable=False, default=""),
    sa.Column("facts", sa.JSON(none_as_null=True)),
    sa.Column("rationale", sa.JSON(none_as_null=True)),
    sa.Column("rationale_note", sa.String),
    sa.Column("followed_call", sa.Boolean),
    sa.Column("alternatives", sa.String),
    sa.Column("exit_conditions", sa.String),
    sa.Column("outcome", sa.String),
    sa.Column("outcome_at", sa.String),
    sa.Column("body", sa.String),
    sa.Column("player_ids", sa.JSON(none_as_null=True)),
    sa.Column("import_key", sa.String),
    sa.UniqueConstraint("league", "season", "import_key", name="uq_decisions_import"),
)
sa.Index("ix_decisions_week_kind", decisions_table.c.league, decisions_table.c.season, decisions_table.c.week,
         decisions_table.c.kind)

# The API's page payloads (api.Cache), written through on every install and loaded expired at boot, so the first open
# of a page after a restart is served at once while it rebuilds behind. A dropped page's row is deleted with it.
page_cache = sa.Table(
    "page_cache", metadata, _league(), _season(),
    sa.Column("key", sa.String, nullable=False),
    sa.Column("payload", sa.JSON, nullable=False),
    sa.Column("built_at", sa.Float, nullable=False),     # epoch seconds
    sa.PrimaryKeyConstraint("league", "season", "key"),
)

# ffl/sources' scrapes (sources.Cached), written through on every good fetch and loaded on a source's first read,
# so a restart serves what is still inside its TTL and keeps the rest as last good. `key` is the cache key as
# JSON (a week, "ros"); `data` is [[name, pos, points], ...].
source_cache = sa.Table(
    "source_cache", metadata, _season(),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("key", sa.String, nullable=False),
    sa.Column("data", sa.JSON, nullable=False),
    sa.Column("fetched_at", sa.Float, nullable=False),   # epoch seconds
    sa.Column("ttl", sa.Float, nullable=False),
    sa.PrimaryKeyConstraint("season", "name", "key"),
)

# The news catalog (ffl/newsdesk/): where items come from, what they said, the stories they were grouped into and which
# players each story moves. Times are ISO strings, so they compare as text.
news_sources_table = sa.Table(
    "news_sources", metadata, _season(),
    sa.Column("id", sa.String, nullable=False),          # site:LAR / espn:LAR / yt:LAR / pod:LAR / bsky:<handle> / pft / rotowire
    sa.Column("kind", sa.String, nullable=False),        # site|espn|youtube|podcast|bluesky|pft|rotowire
    sa.Column("club", sa.String),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("url", sa.String, nullable=False),
    sa.Column("active", sa.Boolean, nullable=False, default=True),
    sa.Column("ok_at", sa.String),                       # last good fetch
    sa.Column("error", sa.String),                       # last failure, None after a good fetch
    sa.Column("failing_since", sa.String),               # first failure of the current run
    sa.PrimaryKeyConstraint("season", "id"),
)

news_items_table = sa.Table(  # the _table suffix where a reader below takes the plain name
    "news_items", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    _season(),
    sa.Column("source_id", sa.String, nullable=False),
    sa.Column("ext_id", sa.String, nullable=False),      # the source's id; `<media id>#<player_id>` for a split video or episode
    sa.Column("url", sa.String, nullable=False),
    sa.Column("published_at", sa.String, nullable=False),
    sa.Column("fetched_at", sa.String, nullable=False),
    sa.Column("title", sa.String, nullable=False),
    sa.Column("text", sa.String, nullable=False),
    sa.Column("club", sa.String),
    sa.Column("at_s", sa.Float),                         # seconds into the video/audio
    sa.Column("player_ids", sa.JSON, nullable=False, default=list),
    sa.Column("jev", sa.JSON(none_as_null=True)),
    sa.Column("tagged_at", sa.String),                   # NULL = waiting to be tagged (or re-tagged after an edit)
    sa.Column("story_id", sa.Integer),
    sa.Column("hash", sa.String, nullable=False),        # sha256 of title + text
    sa.Column("withdrawn_at", sa.String),                # deleted, or 404/410 on a recheck
    sa.UniqueConstraint("season", "source_id", "ext_id", name="uq_news_items_ext"),
)
sa.Index("ix_news_items_story", news_items_table.c.season, news_items_table.c.story_id)
sa.Index("ix_news_items_published", news_items_table.c.season, news_items_table.c.published_at)

# An edited item's earlier text (the item row holds the latest).
news_item_versions = sa.Table(
    "news_item_versions", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    _season(),
    sa.Column("item_id", sa.Integer, nullable=False),
    sa.Column("hash", sa.String, nullable=False),
    sa.Column("title", sa.String, nullable=False),
    sa.Column("text", sa.String, nullable=False),
    sa.Column("seen_at", sa.String, nullable=False),
)
sa.Index("ix_news_item_versions_item", news_item_versions.c.season, news_item_versions.c.item_id)

# A video or episode's whole transcript, kept so matching and tagging can be re-run without refetching.
news_media_table = sa.Table(
    "news_media", metadata, _season(),
    sa.Column("id", sa.String, nullable=False),          # YouTube video id / podcast episode guid
    sa.Column("source_id", sa.String, nullable=False),
    sa.Column("url", sa.String, nullable=False),
    sa.Column("title", sa.String, nullable=False),
    sa.Column("published_at", sa.String, nullable=False),
    sa.Column("segs", sa.JSON, nullable=False),          # [{start, text}, ...]
    sa.Column("fetched_at", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("season", "id"),
)

news_stories = sa.Table(
    "news_stories", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    _season(),
    sa.Column("first_item_id", sa.Integer),
    sa.Column("first_at", sa.String, nullable=False),
    sa.Column("updated_at", sa.String, nullable=False),  # when the app last moved it (a rev, a status): Jev's window
    sa.Column("latest_at", sa.String, nullable=False),   # its newest live source's published_at: the feed's order
    sa.Column("rev", sa.Integer, nullable=False, default=1),
    sa.Column("written_rev", sa.Integer, nullable=False, default=0),   # < rev = the words are stale or not yet written
    # headline refreshes asked since the words were written (Jev: the headline no longer sums the story up). A refresh
    # is rewritten but never bumps rev, so it never pushes
    sa.Column("reword", sa.Integer, nullable=False, default=0, server_default=sa.text("0")),
    sa.Column("headline", sa.String, nullable=False, default=""),
    sa.Column("overview", sa.String, nullable=False, default=""),
    sa.Column("kind", sa.String, nullable=False, default=""),
    sa.Column("status", sa.String, nullable=False, default="live"),    # live|corrected|retracted
    sa.Column("status_at", sa.String),
    sa.Column("status_note", sa.String),                 # which source corrected or withdrew it
)
sa.Index("ix_news_stories_updated", news_stories.c.season, news_stories.c.updated_at)
sa.Index("ix_news_stories_latest", news_stories.c.season, news_stories.c.latest_at)

# Owners are read-time, so there is no ownership column here.
news_story_players = sa.Table(
    "news_story_players", metadata, _season(),
    sa.Column("story_id", sa.Integer, nullable=False),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("effect", sa.String, nullable=False),      # up|down|none
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("prob", sa.Float, nullable=False),
    sa.Column("confidence", sa.Float, nullable=False),
    sa.Column("reason", sa.String),
    sa.Column("flag", sa.Boolean, nullable=False, default=False),
    sa.PrimaryKeyConstraint("season", "story_id", "player_id"),
)
sa.Index("ix_news_story_players_player", news_story_players.c.season, news_story_players.c.player_id)


def url() -> str:
    return os.environ.get("FFL_DB_URL") or f"sqlite:///{DATA / 'ffl.db'}"


_ENGINE: sa.Engine | None = None
_ENGINE_LOCK = threading.Lock()


def _alembic_config():
    from alembic.config import Config
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def _head() -> str:
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(_alembic_config()).get_current_head()


def _make_engine() -> sa.Engine:
    eng = sa.create_engine(url())
    if eng.dialect.name == "sqlite":
        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _rec):
            dbapi_conn.isolation_level = None  # we issue BEGIN ourselves (see "begin" below)
            cur = dbapi_conn.cursor()
            # busy_timeout first: the WAL switch needs a lock, and without a timeout a second connector fails at once
            for p in ("busy_timeout=5000", "journal_mode=WAL", "foreign_keys=ON", "synchronous=NORMAL"):
                cur.execute(f"PRAGMA {p}")
            cur.close()

        @event.listens_for(eng, "begin")
        def _begin(conn):
            conn.exec_driver_sql("BEGIN IMMEDIATE" if conn.info.get("write") else "BEGIN")
    return eng


def engine() -> sa.Engine:
    """The process's engine; the first call checks the schema is at Alembic's head."""
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:  # concurrent API threads at boot must not build (and leak) two engines
            if _ENGINE is None:
                _ENGINE = _build_checked()
    return _ENGINE


def _build_checked() -> sa.Engine:
    u = url()
    # connecting would create an empty file that looks like a DB; a missing one is a missing migration
    if u.startswith("sqlite:///") and not os.path.exists(u.removeprefix("sqlite:///")):
        raise SchemaError(f"{u} does not exist: run: uv run alembic upgrade head")
    eng = _make_engine()
    from alembic.runtime.migration import MigrationContext
    with eng.connect() as c:
        current = MigrationContext.configure(c).get_current_revision()
    if current != _head():
        eng.dispose()
        raise SchemaError(f"{u} is at schema {current!r}, code wants {_head()!r}: run: uv run alembic upgrade head")
    return eng


def reset() -> None:
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is not None:
            _ENGINE.dispose()
        _ENGINE = None


@contextmanager
def _write():
    with engine().connect() as c:
        c.info["write"] = True
        try:
            with c.begin():
                yield c
        finally:
            c.info.pop("write", None)


@contextmanager
def _read():
    with engine().connect() as c:
        yield c


def _upsert(table: sa.Table, rows: list[dict], keys: list[str], update: list[str] | None):
    if engine().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    stmt = insert(table).values(rows)
    if update is None:
        return stmt.on_conflict_do_nothing(index_elements=keys)
    return stmt.on_conflict_do_update(index_elements=keys, set_={c: stmt.excluded[c] for c in update})


def league_rows() -> list[dict]:
    """Every registered league, the home one first."""
    t = leagues_table
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(sa.select(t).order_by(t.c.home.desc(), t.c.added_at, t.c.key))]


def put_league(row: dict) -> None:
    """Upsert a league by key (added_at set on first insert only); a home row demotes every other."""
    with _write() as c:
        _put_league(c, row)


def _put_league(c, row: dict) -> None:
    from datetime import datetime
    t = leagues_table
    row = {**row, "added_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    if row["home"]:
        c.execute(sa.update(t).where(t.c.key != row["key"]).values(home=False))
    c.execute(_upsert(t, [row], ["key"], [k for k in row if k not in ("key", "added_at")]))


def put_league_settings(rows: dict, league_row: dict) -> None:
    """Write formats.rows_for's rows in one transaction: formats insert-if-absent (a key is its content),
    the league's links and settings replaced, its caps' provider limits replaced and my caps kept, and its
    registry row (`league_row`, linked to the lineup format) upserted as put_league does."""
    with _write() as c:
        _put_league(c, league_row)
        c.execute(_upsert(scoring_formats, rows["scoring_formats"], ["key"], None))
        c.execute(_upsert(lineup_formats, rows["lineup_formats"], ["key"], None))
        c.execute(_upsert(league_scoring, rows["league_scoring"], ["league", "pos"], ["scoring_format", "unmapped"]))
        c.execute(_upsert(league_settings_table, rows["league_settings"], ["league", "season"], ["doc", "fetched_at"]))
        c.execute(_upsert(league_caps, rows["league_caps"], ["league", "pos"], ["provider_limit"]))


def league_settings_doc(league: str, season: int) -> dict | None:
    """The stored pieces formats.assemble puts back together: {doc, lineup, caps, tables (pos -> scoring table)};
    None when never stored."""
    s, lf, lc, lg = league_settings_table, lineup_formats, league_caps, leagues_table
    with _read() as c:
        doc = c.execute(sa.select(s.c.doc).where(s.c.league == league, s.c.season == season)).scalar()
        if doc is None:
            return None
        lineup = c.execute(sa.select(lf.c.doc).join(lg, lg.c.lineup_format == lf.c.key)
                           .where(lg.c.key == league)).scalar()
        caps = {r.pos: {"provider_limit": r.provider_limit, "my_cap": r.my_cap}
                for r in c.execute(sa.select(lc).where(lc.c.league == league))}
        ls, sf = league_scoring, scoring_formats
        tables = {r.pos: r.table for r in c.execute(
            sa.select(ls.c.pos, sf.c.table).join(sf, sf.c.key == ls.c.scoring_format).where(ls.c.league == league))}
    return {"doc": doc, "lineup": lineup, "caps": caps, "tables": tables}


def league_scoring_tables(league: str) -> dict[str, dict[str, float]]:
    ls, sf = league_scoring, scoring_formats
    with _read() as c:
        return {r.pos: r.table for r in c.execute(
            sa.select(ls.c.pos, sf.c.table).join(sf, sf.c.key == ls.c.scoring_format).where(ls.c.league == league))}


def caps_rows(league: str) -> dict[str, dict]:
    """{pos: {provider_limit, my_cap}} for the league; empty when nothing is stored."""
    t = league_caps
    with _read() as c:
        return {r.pos: {"provider_limit": r.provider_limit, "my_cap": r.my_cap}
                for r in c.execute(sa.select(t).where(t.c.league == league))}


def put_my_cap(league: str, pos: str, my_cap: int | None) -> None:
    """Set my cap at a position, keeping the league's limit (a row without one is inserted with none)."""
    with _write() as c:
        c.execute(_upsert(league_caps, [{"league": league, "pos": pos, "provider_limit": None, "my_cap": my_cap}],
                          ["league", "pos"], ["my_cap"]))


def strategy_row(key: str) -> dict | None:
    """The latest version of a strategy preset; None when the key has none."""
    t = strategies
    with _read() as c:
        r = c.execute(sa.select(t).where(t.c.key == key).order_by(t.c.version.desc()).limit(1)).first()
    return dict(r._mapping) if r else None


def strategy_rows() -> list[dict]:
    """Every preset's every version, by key then version."""
    t = strategies
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(sa.select(t).order_by(t.c.key, t.c.version))]


def seed_my_caps(league: str, caps: dict[str, int]) -> None:
    """My cap at each position that has none yet, in one transaction (a set cap is never touched)."""
    t = league_caps
    with _write() as c:
        c.execute(_upsert(t, [{"league": league, "pos": p, "provider_limit": None, "my_cap": None} for p in caps],
                          ["league", "pos"], None))
        for p, n in caps.items():
            c.execute(sa.update(t).where(t.c.league == league, t.c.pos == p, t.c.my_cap.is_(None)).values(my_cap=n))


def league_scoring_keys(league: str) -> dict[str, str]:
    """{pos: scoring_formats key} linked to the league; empty when its settings were never stored."""
    t = league_scoring
    with _read() as c:
        return {r.pos: r.scoring_format for r in c.execute(sa.select(t.c.pos, t.c.scoring_format)
                                                           .where(t.c.league == league))}


def espn_pool_rows(keys: list[str], season: int) -> dict[str, list[dict]]:
    """{scoring_format: rows} for the keys stored for the season (a key never stored is left out)."""
    t = espn_pools
    with _read() as c:
        return {r.scoring_format: r.rows for r in c.execute(
            sa.select(t.c.scoring_format, t.c.rows).where(t.c.season == season, t.c.scoring_format.in_(keys)))}


def espn_pool_fetched(keys: list[str], season: int) -> dict[str, str]:
    """{scoring_format: fetched_at} for the keys stored for the season."""
    t = espn_pools
    with _read() as c:
        return {r.scoring_format: r.fetched_at for r in c.execute(
            sa.select(t.c.scoring_format, t.c.fetched_at).where(t.c.season == season, t.c.scoring_format.in_(keys)))}


def put_espn_pool(pools: dict[str, list[dict]], season: int, fetched_at: str | None = None) -> None:
    """Replace the season's rows under each scoring format, in one transaction."""
    if not pools:
        return
    at = fetched_at or _now()
    with _write() as c:
        c.execute(_upsert(espn_pools, [{"scoring_format": k, "season": season, "rows": v, "fetched_at": at}
                                       for k, v in pools.items()],
                          ["scoring_format", "season"], ["rows", "fetched_at"]))


def sleeper_season_raw(season: int) -> list | None:
    with _read() as c:
        return c.execute(sa.select(sleeper_season.c.raw).where(sleeper_season.c.season == season)).scalar()


def put_sleeper_season(season: int, raw: list, fetched_at: str | None = None) -> None:
    with _write() as c:
        c.execute(_upsert(sleeper_season, [{"season": season, "raw": raw, "fetched_at": fetched_at or _now()}],
                          ["season"], ["raw", "fetched_at"]))


def adp_feed(fmt: str, teams: int, year: int) -> dict | None:
    """{rows, meta, fetched_at} of one stored FFC feed; None when never fetched."""
    t = adp_feeds
    with _read() as c:
        r = c.execute(sa.select(t.c.rows, t.c.meta, t.c.fetched_at)
                      .where(t.c.fmt == fmt, t.c.teams == teams, t.c.year == year)).first()
    return dict(r._mapping) if r else None


def put_adp_feed(fmt: str, teams: int, year: int, rows: list[dict], meta: dict, fetched_at: str | None = None) -> None:
    with _write() as c:
        c.execute(_upsert(adp_feeds, [{"fmt": fmt, "teams": teams, "year": year, "rows": rows, "meta": meta,
                                       "fetched_at": fetched_at or _now()}],
                          ["fmt", "teams", "year"], ["rows", "meta", "fetched_at"]))


def _league_key(ref) -> str:
    """The league key of a LeagueRef; None is the home league, never FFL_LEAGUE: the season side still reads ESPN
    through home, so a draft shell's FFL_LEAGUE must not file home's data elsewhere. TRANSITIONAL (4.12 removes the
    default). Resolved before a transaction opens: the registry read is a read of its own."""
    if ref is not None:
        return ref.key
    from . import leagues  # here: leagues imports this module
    return leagues.home().key


def add_plays(rows: list[dict], season: int = SEASON, *, ref=None) -> int:
    """Insert the play-feed rows not already stored (key: league, season, week, player, at, total). Returns how
    many were new."""
    if not rows:
        return 0
    league = _league_key(ref)
    vals = [{"league": league, "season": season, "week": r["week"], "at": r["at"], "player_id": r["player_id"],
             "team_id": r.get("team_id"), "total": r["total"], "body": r} for r in rows]
    with _write() as c:
        stmt = _upsert(plays, vals, _KEYS["plays"], None).returning(plays.c.id)
        return len(c.execute(stmt).all())


def plays_rows(week: int, season: int = SEASON, *, ref=None) -> list[dict]:
    """One week's play-feed rows, oldest first."""
    t = plays
    q = sa.select(t.c.body).where(t.c.league == _league_key(ref), t.c.season == season, t.c.week == week) \
        .order_by(t.c.id)
    with _read() as c:
        return list(c.execute(q).scalars())


def update_plays(rows: list[dict], season: int = SEASON, *, ref=None) -> None:
    """Rewrite stored play-feed rows' bodies in place, each found by its key (week, player, at, total)."""
    t, league = plays, _league_key(ref)
    with _write() as c:
        for r in rows:
            c.execute(t.update().where(t.c.league == league, t.c.season == season, t.c.week == r["week"],
                                       t.c.player_id == r["player_id"], t.c.at == r["at"], t.c.total == r["total"])
                      .values(body=r))


CHUNK = 400  # rows per multi-row statement: SQLite caps a statement's bound variables


def put_nfl_plays(rows: list[dict], season: int = SEASON) -> None:
    """Insert or replace plays (playbook entries) by play id."""
    vals = [{"season": season, "play_id": r["play_id"], "week": r["week"], "game_id": r["game_id"],
             "source": r["source"], "body": r} for r in rows]
    with _write() as c:
        for i in range(0, len(vals), CHUNK):
            c.execute(_upsert(nfl_plays, vals[i:i + CHUNK], ["season", "play_id"], ["week", "game_id", "source", "body"]))


def nfl_plays_rows(week: int, season: int = SEASON) -> list[dict]:
    """One week's stored plays (playbook entries)."""
    q = sa.select(nfl_plays.c.body).where(nfl_plays.c.season == season, nfl_plays.c.week == week)
    with _read() as c:
        return list(c.execute(q).scalars())


def put_called_back(put: list[dict], drop: list[tuple[str, int]], season: int = SEASON) -> None:
    """Write called-back rows (by play and player) and delete the (play_id, player_id) pairs in `drop`."""
    t = called_back
    vals = [{"season": season, "play_id": r["play_id"], "player_id": r["player_id"], "week": r["week"], "body": r}
            for r in put]
    with _write() as c:
        for i in range(0, len(vals), CHUNK):
            c.execute(_upsert(t, vals[i:i + CHUNK], ["season", "play_id", "player_id"], ["week", "body"]))
        for play_id, player_id in drop:
            c.execute(t.delete().where(t.c.season == season, t.c.play_id == play_id, t.c.player_id == player_id))


def called_back_rows(week: int, season: int = SEASON) -> list[dict]:
    q = sa.select(called_back.c.body).where(called_back.c.season == season, called_back.c.week == week)
    with _read() as c:
        return list(c.execute(q).scalars())


_CORR_COLS = ("week", "player_id", "team_id", "old", "new", "at")


def _corr_vals(rows, season: int, league: str) -> list[dict]:
    return [{"league": league, "season": season, **{k: r.get(k) for k in _CORR_COLS}} for r in rows]


def box_doc(week: int, season: int = SEASON, *, ref=None) -> dict | None:
    """One week's stored box-score document: {doc, hash, fetched_at, settled, final}, or None."""
    t = box_scores_table
    q = sa.select(t.c.doc, t.c.hash, t.c.fetched_at, t.c.settled, t.c.final).where(
        t.c.league == _league_key(ref), t.c.season == season, t.c.week == week)
    with _read() as c:
        r = c.execute(q).first()
    return dict(r._mapping) if r else None


def box_weeks(season: int = SEASON, *, ref=None) -> dict[int, dict]:
    """Every stored week -> {fetched_at, settled, final}, without the documents."""
    t = box_scores_table
    q = sa.select(t.c.week, t.c.fetched_at, t.c.settled, t.c.final).where(t.c.league == _league_key(ref),
                                                                          t.c.season == season)
    with _read() as c:
        return {r.week: {"fetched_at": r.fetched_at, "settled": r.settled, "final": r.final} for r in c.execute(q)}


def put_box_doc(week: int, doc: dict, hash: str, fetched_at: float, settled: bool | None = None,
                corrections=(), season: int = SEASON, final: bool = False, *, ref=None) -> bool:
    """Store a read of one week's box scores in one transaction: insert the row if absent; else always set
    fetched_at (the correction pass's throttle and the current week's 60 s hit read it) and final (the read
    decides: made after the week finished or not), doc and hash only when the hash differs, settled when not
    None. `corrections` go into box_score_corrections in the same transaction. Returns True when doc/hash
    were (re)written."""
    t, league = box_scores_table, _league_key(ref)
    key = (t.c.league == league, t.c.season == season, t.c.week == week)
    with _write() as c:
        old = c.execute(sa.select(t.c.hash).where(*key)).first()
        if old is None:
            c.execute(t.insert().values(league=league, season=season, week=week, doc=doc, hash=hash,
                                        fetched_at=fetched_at, settled=bool(settled), final=final))
            wrote = True
        else:
            vals: dict = {"fetched_at": fetched_at, "final": final}
            wrote = old.hash != hash
            if wrote:
                vals.update(doc=doc, hash=hash)
            if settled is not None:
                vals["settled"] = settled
            c.execute(t.update().where(*key).values(**vals))
        if corrections:
            c.execute(box_score_corrections.insert(), _corr_vals(corrections, season, league))
    return wrote


def expire_live_boxes(season: int = SEASON, *, ref=None) -> None:
    """fetched_at = 0 on every non-final row: after a league move no stored current week counts as a 60 s hit.
    A final row keeps its fetched_at, the correction pass's hourly throttle."""
    t, league = box_scores_table, _league_key(ref)
    with _write() as c:
        c.execute(t.update().where(t.c.league == league, t.c.season == season, t.c.final.is_(False))
                  .values(fetched_at=0.0))


def gap_rows(week: int, ids, season: int = SEASON, *, ref=None) -> dict[int, list[dict]]:
    """The stored gap-week game rows of those players for a week (only the ones stored)."""
    ids = list(ids)
    if not ids:
        return {}
    t = gap_games_table
    q = sa.select(t.c.player_id, t.c.rows).where(t.c.league == _league_key(ref), t.c.season == season,
                                                 t.c.week == week, t.c.player_id.in_(ids))
    with _read() as c:
        return {r.player_id: r.rows for r in c.execute(q)}


def gap_ids(week: int, season: int = SEASON, *, ref=None) -> list[int]:
    t = gap_games_table
    q = sa.select(t.c.player_id).where(t.c.league == _league_key(ref), t.c.season == season, t.c.week == week) \
        .order_by(t.c.player_id)
    with _read() as c:
        return list(c.execute(q).scalars())


def put_gap_rows(week: int, rows: dict[int, list[dict]], fetched_at: float, season: int = SEASON, *, ref=None) -> None:
    """Upsert (league, season, week, player_id) -> rows, fetched_at."""
    if not rows:
        return
    league = _league_key(ref)
    vals = [{"league": league, "season": season, "week": week, "player_id": pid, "rows": r, "fetched_at": fetched_at}
            for pid, r in rows.items()]
    with _write() as c:
        c.execute(_upsert(gap_games_table, vals, ["league", "season", "week", "player_id"], ["rows", "fetched_at"]))


def add_box_corrections(rows: list[dict], season: int = SEASON, *, ref=None) -> None:
    """Log gap-week corrections (team_id None)."""
    if not rows:
        return
    league = _league_key(ref)
    with _write() as c:
        c.execute(box_score_corrections.insert(), _corr_vals(rows, season, league))


def box_corrections(season: int = SEASON, week: int | None = None, *, ref=None) -> list[dict]:
    """Logged corrections, oldest first: week, player_id, team_id, old, new, at."""
    t = box_score_corrections
    q = sa.select(*[t.c[k] for k in _CORR_COLS]).where(t.c.league == _league_key(ref), t.c.season == season) \
        .order_by(t.c.id)
    if week is not None:
        q = q.where(t.c.week == week)
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def _tx_player_id(tx_id: str) -> int | None:
    parts = tx_id.split("-", 3)
    try:
        return int(parts[3]) if len(parts) == 4 else None
    except ValueError:
        return None


_TX_COLS = ("id", "date", "type", "team", "player", "player_id", "action", "bid", "reasoning")


def add_transactions(rows: list[dict], season: int = SEASON, *, ref=None) -> list[dict]:
    """Insert the rows not already logged (key: league, season, id). Returns the rows that were new, as
    transactions() shapes them: found by RETURNING, since rowcount is unreliable for a multi-row
    ON CONFLICT DO NOTHING. notify queues a notice for each."""
    if not rows:
        return []
    league = _league_key(ref)
    vals = [{"league": league, "season": season, "id": r["id"], "date": r["date"], "type": r["type"],
             "team": r["team"], "player": r["player"], "player_id": _tx_player_id(r["id"]), "action": r["action"],
             "bid": int(r.get("bid") or 0), "reasoning": r.get("reasoning") or ""} for r in rows]
    t = transactions_table
    with _write() as c:
        new = c.execute(_upsert(t, vals, _KEYS["transactions"], None).returning(*[t.c[k] for k in _TX_COLS])).all()
    return [dict(r._mapping) for r in new]


def transactions(season: int = SEASON, *, ref=None) -> list[dict]:
    t = transactions_table
    # rowid is SQLite-only (insert order = the CSV's row order); under Postgres order by date, id
    q = sa.select(*[t.c[k] for k in _TX_COLS]).where(t.c.league == _league_key(ref), t.c.season == season) \
        .order_by(sa.text("rowid"))
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


_RESULT_COLS = ("week", "opponent", "my_score", "opp_score", "result")


def put_results(rows: list[dict], season: int = SEASON, *, ref=None) -> None:
    """Upsert per week. A stale League that knows fewer weeks only rewrites the ones it has."""
    if rows:
        league = _league_key(ref)
        with _write() as c:
            c.execute(_upsert(results, [{"league": league, "season": season, **r} for r in rows], _KEYS["results"],
                              ["opponent", "my_score", "opp_score", "result"]))


def results_rows(season: int = SEASON, *, ref=None) -> list[dict]:
    t = results
    q = sa.select(*[t.c[k] for k in _RESULT_COLS]).where(t.c.league == _league_key(ref), t.c.season == season) \
        .order_by(t.c.week)
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


_PICK_COLS = ("overall", "round", "pick", "team_id", "team", "player_id", "player", "pos", "nfl",
              "adp_2qb", "board_rank_at_pick", "proj", "ts")


def _league_season(ref, season: int | None = None):
    """(league key, season) for a LeagueRef; None is the current league (`leagues.current()`), its season."""
    if ref is None:
        from . import leagues  # here: leagues imports this module
        ref = leagues.current()
    return ref.key, season if season is not None else ref.season


def put_picks(rows: list[dict], ref=None, season: int | None = None) -> None:
    """Upsert per overall pick, in the league `ref` names. team and player are NOT NULL: the watcher can hand
    over None for a team it does not know."""
    if rows:
        league, season = _league_season(ref, season)
        rows = [{"league": league, "season": season, **{k: r.get(k) for k in _PICK_COLS}} for r in rows]
        for r in rows:
            r["team"], r["player"] = r["team"] or "", r["player"] or ""
        with _write() as c:
            c.execute(_upsert(draft_picks, rows, ["league", "season", "overall"],
                              [k for k in _PICK_COLS if k != "overall"]))


def picks(ref=None, season: int | None = None) -> list[dict]:
    """The league's draft log in pick order (the `_PICK_COLS`, no league or season)."""
    league, season = _league_season(ref, season)
    t = draft_picks
    q = sa.select(*[t.c[k] for k in _PICK_COLS]).where(t.c.league == league, t.c.season == season) \
        .order_by(t.c.overall)
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def put_draft_feed(ref, picks: list[dict], offset: int) -> None:
    """Replace the league's raw pick feed (every pick the room's socket has shown, and the offset still missing)."""
    league, season = _league_season(ref)
    row = {"league": league, "season": season, "picks": picks, "offset": offset, "updated_at": _now()}
    with _write() as c:
        c.execute(_upsert(draft_feeds, [row], ["league", "season"], ["picks", "offset", "updated_at"]))


def draft_feed(ref) -> tuple[list[dict], int]:
    """(picks, offset) of the league's pick feed; ([], 0) before its draft room was ever watched."""
    league, season = _league_season(ref)
    t = draft_feeds
    with _read() as c:
        r = c.execute(sa.select(t.c.picks, t.c.offset).where(t.c.league == league, t.c.season == season)).first()
    return (list(r.picks), int(r.offset)) if r else ([], 0)


def put_draft_state(ref, doc: dict) -> None:
    """Replace the watcher's state for the league. `doc` must be plain JSON (the watcher round-trips it)."""
    league, season = _league_season(ref)
    row = {"league": league, "season": season, "doc": doc, "updated_at": _now()}
    with _write() as c:
        c.execute(_upsert(draft_states, [row], ["league", "season"], ["doc", "updated_at"]))


def draft_state(ref) -> dict | None:
    """The watcher's latest state for the league; None when no watcher has run on it."""
    league, season = _league_season(ref)
    t = draft_states
    with _read() as c:
        return c.execute(sa.select(t.c.doc).where(t.c.league == league, t.c.season == season)).scalar()


def picks_in(path, league: str) -> list[dict]:
    """The league's latest-season draft log (the `_PICK_COLS`) in another SQLite file, opened read-only: a practice
    run's own `FFL_DATA_DIR` DB, kept before it is deleted (`ffl.replay --keep`)."""
    eng = sa.create_engine(f"sqlite:///file:{path}?mode=ro&uri=true")
    t = draft_picks
    try:
        with eng.connect() as c:
            season = c.execute(sa.select(sa.func.max(t.c.season)).where(t.c.league == league)).scalar()
            q = sa.select(*[t.c[k] for k in _PICK_COLS]).where(t.c.league == league, t.c.season == season) \
                .order_by(t.c.overall)
            return [dict(r._mapping) for r in c.execute(q)] if season is not None else []
    finally:
        eng.dispose()


def put_draft_log(source: str, log_id: str, rows: list[dict], meta: dict | None = None,
                  added_at: str | None = None) -> bool:
    """Store a finished draft's pick log unless (source, log_id) is already stored; True when it was added.
    `rows` must be plain JSON (`adp.records` of a frame)."""
    row = {"source": source, "log_id": str(log_id), "meta": meta, "rows": rows, "added_at": added_at or _now()}
    with _write() as c:
        return c.execute(_upsert(draft_logs, [row], ["source", "log_id"], None)).rowcount > 0


def draft_log_ids(source: str) -> set[str]:
    with _read() as c:
        return set(c.execute(sa.select(draft_logs.c.log_id).where(draft_logs.c.source == source)).scalars())


def draft_logs_rows(source: str | None = None, log_id: str | None = None) -> list[dict]:
    """Stored draft logs ({source, log_id, meta, rows, added_at}) by source and id; None matches any."""
    t = draft_logs
    q = sa.select(t)
    if source is not None:
        q = q.where(t.c.source == source)
    if log_id is not None:
        q = q.where(t.c.log_id == str(log_id))
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q.order_by(t.c.source, t.c.log_id))]


def add_trace(at: str, doc: dict) -> None:
    with _write() as c:
        c.execute(sa.insert(traces).values(at=at, doc=doc))


def trace_rows() -> list[dict]:
    """Every trace row in arrival order, as the old trace.jsonl line: {at, **doc}."""
    with _read() as c:
        return [{"at": r.at, **r.doc} for r in c.execute(sa.select(traces).order_by(traces.c.id))]


def props_snapshot_exists(week: int, kind: str) -> bool:
    t = props_snapshots
    with _read() as c:
        return c.execute(sa.select(t.c.week).where(t.c.week == week, t.c.kind == kind)).first() is not None


def put_props_snapshot(week: int, kind: str, doc: dict) -> None:
    """Replace the week's `kind` snapshot (a forced pull spends the credits again and overwrites it)."""
    with _write() as c:
        c.execute(_upsert(props_snapshots, [{"week": week, "kind": kind, "doc": doc}], ["week", "kind"], ["doc"]))


def props_snapshot_docs() -> list[dict]:
    t = props_snapshots
    with _read() as c:
        return list(c.execute(sa.select(t.c.doc).order_by(t.c.week, t.c.kind)).scalars())


def put_props_raw(week: int, kind: str, event_id: str, doc) -> None:
    row = {"week": week, "kind": kind, "event_id": event_id, "doc": doc}
    with _write() as c:
        c.execute(_upsert(props_raw, [row], ["week", "kind", "event_id"], ["doc"]))


def props_raw_docs(week: int, kind: str) -> dict[str, object]:
    """{event id: The Odds API's answer} of one pull."""
    t = props_raw
    with _read() as c:
        return {r.event_id: r.doc for r in c.execute(sa.select(t.c.event_id, t.c.doc)
                                                      .where(t.c.week == week, t.c.kind == kind))}


def put_outlooks(week: int, kind: str, entries: dict[int, dict], season: int = SEASON, *, ref=None) -> None:
    """Upsert each player's entry; never deletes."""
    if not entries:
        return
    from datetime import datetime
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    league = _league_key(ref)
    vals = [{"league": league, "season": season, "week": week, "kind": kind, "player_id": int(pid), "entry": e,
             "updated": now} for pid, e in entries.items()]
    with _write() as c:
        c.execute(_upsert(outlooks, vals, _KEYS["outlooks"], ["entry", "updated"]))


def outlooks_for(week: int, kind: str, season: int = SEASON, *, ref=None) -> dict[int, dict]:
    t = outlooks
    q = sa.select(t.c.player_id, t.c.entry).where(
        t.c.league == _league_key(ref), t.c.season == season, t.c.week == week, t.c.kind == kind)
    with _read() as c:
        return {pid: e for pid, e in c.execute(q)}


_CAL_COLS = ("player_id", "name", "pos", "model", "l3", "weight", "games_left")


def put_snapshot(week: int, k: float, rows: list[dict], season: int = SEASON, *, ref=None) -> bool:
    """The ROS week's snapshot, once: the parent row is DO NOTHING, and rows are written only when it was new."""
    from datetime import datetime
    league = _league_key(ref)
    with _write() as c:
        made = c.execute(_upsert(calibration_snapshots, [{"league": league, "season": season, "week": week, "k": k,
                                 "taken_at": datetime.now().isoformat(timespec="seconds")}],
                                 ["league", "season", "week"], None).returning(calibration_snapshots.c.week)).all()
        if made and rows:
            c.execute(calibration_rows.insert(), [{"league": league, "season": season, "week": week,
                                                   **{x: r[x] for x in _CAL_COLS}} for r in rows])
    return bool(made)


def snapshots(season: int = SEASON, *, ref=None) -> list[dict]:
    """Every week's snapshot, by week, in the shape the week JSON files had."""
    cs, cr, league = calibration_snapshots, calibration_rows, _league_key(ref)
    with _read() as c:
        snaps = [{"week": w, "k": k, "rows": []} for w, k in c.execute(
            sa.select(cs.c.week, cs.c.k).where(cs.c.league == league, cs.c.season == season).order_by(cs.c.week))]
        by_week = {s["week"]: s for s in snaps}
        for r in c.execute(sa.select(cr).where(cr.c.league == league, cr.c.season == season)
                           .order_by(sa.text("rowid"))):
            by_week[r.week]["rows"].append({x: getattr(r, x) for x in _CAL_COLS})
    return snaps


def put_sigma(fit: dict, season: int = SEASON, fitted_at: str | None = None) -> None:
    from datetime import datetime
    with _write() as c:
        c.execute(sigma_fits.insert().values(season=season, fit=fit,
                                             fitted_at=fitted_at or datetime.now().isoformat(timespec="seconds")))


def latest_sigma(season: int = SEASON) -> dict | None:
    q = sa.select(sigma_fits.c.fit).where(sigma_fits.c.season == season).order_by(sigma_fits.c.fitted_at.desc()).limit(1)
    with _read() as c:
        return c.execute(q).scalar()


def put_projection_rows(week: int, kind: str, rows: list[dict], season: int = SEASON, *, ref=None) -> int:
    """Upsert each source's points for each player (rows: player_id, source, points). Returns rows written."""
    if not rows:
        return 0
    at, league = _now(), _league_key(ref)
    vals = [{"league": league, "season": season, "week": week, "kind": kind, "player_id": r["player_id"],
             "source": r["source"], "points": float(r["points"]), "taken_at": at} for r in rows]
    with _write() as c:
        c.execute(_upsert(projection_rows_table, vals, ["league", "season", "week", "kind", "player_id", "source"],
                          ["points", "taken_at"]))
    return len(vals)


def projection_rows(kind: str, season: int = SEASON, week: int | None = None, *, ref=None) -> list[dict]:
    t = projection_rows_table
    q = sa.select(t.c.week, t.c.player_id, t.c.source, t.c.points, t.c.taken_at).where(
        t.c.league == _league_key(ref), t.c.season == season, t.c.kind == kind) \
        .order_by(t.c.week, t.c.player_id, t.c.source)
    if week is not None:
        q = q.where(t.c.week == week)
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def put_source_weights(fit: dict, season: int = SEASON, fitted_at: str | None = None) -> str:
    """Store a fitted set of per-source weights, inactive. Returns its fitted_at key."""
    from datetime import datetime
    at = fitted_at or datetime.now().isoformat(timespec="seconds")
    with _write() as c:
        c.execute(source_weights.insert().values(season=season, fitted_at=at, fit=fit, active=False))
    return at


def source_weight_fits(season: int = SEASON) -> list[dict]:
    q = (sa.select(source_weights.c.fitted_at, source_weights.c.fit, source_weights.c.active)
         .where(source_weights.c.season == season).order_by(source_weights.c.fitted_at.desc()))
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def activate_source_weights(fitted_at: str, season: int = SEASON) -> bool:
    """Make one fit the only active one. False (and no change) when that fit is absent."""
    t = source_weights
    with _write() as c:
        if c.execute(sa.select(t.c.fitted_at).where(t.c.season == season, t.c.fitted_at == fitted_at)).first() is None:
            return False
        c.execute(t.update().where(t.c.season == season).values(active=False))
        c.execute(t.update().where(t.c.season == season, t.c.fitted_at == fitted_at).values(active=True))
    return True


def active_source_weights(season: int = SEASON) -> dict | None:
    q = sa.select(source_weights.c.fit).where(source_weights.c.season == season, source_weights.c.active.is_(True))
    with _read() as c:
        return c.execute(q).scalar()


def put_claim_plan(plan: dict, season: int = SEASON, *, ref=None) -> None:
    """The week's waiver claim plan, replaced whole (upsert by week)."""
    from datetime import datetime
    league = _league_key(ref)
    with _write() as c:
        c.execute(_upsert(claim_plans, [{"league": league, "season": season, "week": plan["week"], "plan": plan,
                                         "updated": datetime.now().isoformat(timespec="seconds")}],
                          _KEYS["claim_plans"], ["plan", "updated"]))


def claim_plan(week: int, season: int = SEASON, *, ref=None) -> dict | None:
    t = claim_plans
    q = sa.select(t.c.plan).where(t.c.league == _league_key(ref), t.c.season == season, t.c.week == week)
    with _read() as c:
        return c.execute(q).scalar()


def put_sit(week: int, player_id: int, sat: bool, season: int = SEASON, *, ref=None) -> None:
    """Sit him for the week (a repeat is a no-op) or stand him back up (delete)."""
    from datetime import datetime
    league = _league_key(ref)
    with _write() as c:
        if sat:
            c.execute(_upsert(sits, [{"league": league, "season": season, "week": week, "player_id": player_id,
                                      "at": datetime.now().astimezone().isoformat(timespec="seconds")}],
                              ["league", "season", "week", "player_id"], None))
        else:
            c.execute(sa.delete(sits).where(sits.c.league == league, sits.c.season == season, sits.c.week == week,
                                            sits.c.player_id == player_id))


def watched_ids(season: int = SEASON, *, ref=None) -> list[int]:
    t = watchlist
    q = (sa.select(t.c.player_id).where(t.c.league == _league_key(ref), t.c.season == season)
         .order_by(t.c.added_at, t.c.player_id))
    with _read() as c:
        return list(c.execute(q).scalars())


def put_watch(player_id: int, watched: bool, season: int = SEASON, *, ref=None) -> None:
    """Watch him (a repeat is a no-op) or stop (delete the watch; his signals stay)."""
    t, league = watchlist, _league_key(ref)
    with _write() as c:
        if watched:
            c.execute(_upsert(t, [{"league": league, "season": season, "player_id": player_id, "added_at": _now()}],
                              ["league", "season", "player_id"], None))
        else:
            c.execute(sa.delete(t).where(t.c.league == league, t.c.season == season, t.c.player_id == player_id))


_SIGNAL_COLS = ("key", "player_id", "kind", "text", "at")


def add_watch_signals(rows: list[dict], season: int = SEASON, *, ref=None) -> list[dict]:
    """Store the signals not already known (key: league, season, key). Returns only the rows newly inserted."""
    if not rows:
        return []
    t, league = watch_signals_table, _league_key(ref)
    with _write() as c:
        known = set(c.execute(sa.select(t.c.key).where(t.c.league == league, t.c.season == season)).scalars())
        new = []
        for r in rows:
            if r["key"] not in known:
                known.add(r["key"])
                new.append({k: r[k] for k in _SIGNAL_COLS})
        if new:
            c.execute(_upsert(t, [{"league": league, "season": season, **v} for v in new],
                              ["league", "season", "key"], None))
    return new


def watch_signals(since: str, season: int = SEASON, *, ref=None) -> list[dict]:
    """Signals with `at` >= since, newest first."""
    t = watch_signals_table
    q = (sa.select(*[t.c[k] for k in _SIGNAL_COLS])
         .where(t.c.league == _league_key(ref), t.c.season == season, t.c.at >= since)
         .order_by(t.c.at.desc(), t.c.key))
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def seat_map(season: int = SEASON, *, ref=None) -> dict[int, int]:
    q = sa.select(seats.c.player_id, seats.c.seat_id).where(seats.c.league == _league_key(ref), seats.c.season == season)
    with _read() as c:
        return dict(c.execute(q).all())


def put_seats(rows: dict[int, int], season: int = SEASON, *, ref=None) -> None:
    """Upsert player -> seat in one transaction."""
    if not rows:
        return
    league = _league_key(ref)
    with _write() as c:
        c.execute(_upsert(seats, [{"league": league, "season": season, "player_id": p, "seat_id": s}
                                  for p, s in rows.items()],
                          ["league", "season", "player_id"], ["seat_id"]))


def sat_ids(week: int, season: int = SEASON, *, ref=None) -> set[int]:
    q = sa.select(sits.c.player_id).where(sits.c.league == _league_key(ref), sits.c.season == season,
                                          sits.c.week == week)
    with _read() as c:
        return set(c.execute(q).scalars())


def _now() -> str:
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")


def put_subscription(endpoint: str, keys: dict, ua: str = "") -> None:
    """One device's push subscription; the same endpoint again replaces its keys (browsers rotate them)."""
    with _write() as c:
        c.execute(_upsert(push_subscriptions, [{"endpoint": endpoint, "keys": keys, "ua": ua, "created_at": _now()}],
                          ["endpoint"], ["keys", "ua"]))


def drop_subscription(endpoint: str) -> None:
    with _write() as c:
        c.execute(sa.delete(push_subscriptions).where(push_subscriptions.c.endpoint == endpoint))


def subscriptions() -> list[dict]:
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(sa.select(push_subscriptions))]


def add_notices(rows: list[dict], season: int = SEASON, *, ref=None) -> int:
    """Queue the notices not already known (key: league, season, key). Returns how many were new."""
    if not rows:
        return 0
    league = _league_key(ref)
    vals = [{"league": league, "season": season, **{k: r[k] for k in ("key", "kind", "title", "body", "url", "created_at")}}
            for r in rows]
    with _write() as c:
        return len(c.execute(_upsert(notices, vals, ["league", "season", "key"], None).returning(notices.c.key)).all())


_NOTICE_COLS = ("key", "kind", "title", "body", "url", "created_at", "sent_at", "outcome")


def unsent_notices(season: int = SEASON, *, ref=None) -> list[dict]:
    t = notices
    q = (sa.select(*[t.c[k] for k in _NOTICE_COLS])
         .where(t.c.league == _league_key(ref), t.c.season == season, t.c.sent_at.is_(None))
         .order_by(t.c.created_at, t.c.key))
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def mark_notices(keys: list[str], outcome: str, at: str, season: int = SEASON, *, ref=None) -> None:
    if not keys:
        return
    t, league = notices, _league_key(ref)
    with _write() as c:
        c.execute(sa.update(t).where(t.c.league == league, t.c.season == season, t.c.key.in_(keys))
                  .values(sent_at=at, outcome=outcome))


def recent_notices(limit: int = 20, season: int = SEASON, *, ref=None) -> list[dict]:
    t = notices
    q = (sa.select(*[t.c[k] for k in _NOTICE_COLS]).where(t.c.league == _league_key(ref), t.c.season == season)
         .order_by(t.c.created_at.desc(), t.c.key.desc()).limit(limit))
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def get_state(key: str, default=None, season: int = SEASON):
    q = sa.select(notify_state.c.value).where(notify_state.c.season == season, notify_state.c.key == key)
    with _read() as c:
        v = c.execute(q).scalar()
    return default if v is None else v


def put_state(key: str, value, season: int = SEASON) -> None:
    with _write() as c:
        c.execute(_upsert(notify_state, [{"season": season, "key": key, "value": value}], ["season", "key"], ["value"]))


def get_league_state(key: str, default=None, season: int = SEASON, *, ref=None):
    t = league_state
    q = sa.select(t.c.value).where(t.c.league == _league_key(ref), t.c.season == season, t.c.key == key)
    with _read() as c:
        v = c.execute(q).scalar()
    return default if v is None else v


def put_league_state(key: str, value, season: int = SEASON, *, ref=None) -> None:
    league = _league_key(ref)
    with _write() as c:
        c.execute(_upsert(league_state, [{"league": league, "season": season, "key": key, "value": value}],
                          ["league", "season", "key"], ["value"]))


def put_page(key: str, payload, built_at: float, season: int = SEASON, *, ref=None) -> None:
    league = _league_key(ref)
    with _write() as c:
        c.execute(_upsert(page_cache, [{"league": league, "season": season, "key": key, "payload": payload,
                                        "built_at": built_at}], ["league", "season", "key"], ["payload", "built_at"]))


def drop_pages(keys: list[str], season: int = SEASON, *, ref=None) -> None:
    if not keys:
        return
    league = _league_key(ref)
    with _write() as c:
        c.execute(page_cache.delete().where(page_cache.c.league == league, page_cache.c.season == season,
                                            page_cache.c.key.in_(keys)))


def drop_page_prefixes(prefixes: tuple[str, ...], season: int = SEASON, *, ref=None) -> None:
    """Delete every row keyed `prefix` or `prefix:...`."""
    k = page_cache.c.key
    match = sa.or_(*[sa.or_(k == p, k.startswith(p + ":", autoescape=True)) for p in prefixes])
    league = _league_key(ref)
    with _write() as c:
        c.execute(page_cache.delete().where(page_cache.c.league == league, page_cache.c.season == season, match))


def clear_pages(season: int = SEASON, *, ref=None) -> None:
    league = _league_key(ref)
    with _write() as c:
        c.execute(page_cache.delete().where(page_cache.c.league == league, page_cache.c.season == season))


def pages(season: int = SEASON, *, ref=None) -> list[dict]:
    """Every stored page: {key, payload, built_at}."""
    t = page_cache
    q = sa.select(t.c.key, t.c.payload, t.c.built_at).where(t.c.league == _league_key(ref), t.c.season == season)
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def put_source(name: str, key: str, data: list, fetched_at: float, ttl: float, season: int = SEASON) -> None:
    with _write() as c:
        c.execute(_upsert(source_cache, [{"season": season, "name": name, "key": key, "data": data,
                                          "fetched_at": fetched_at, "ttl": ttl}],
                          ["season", "name", "key"], ["data", "fetched_at", "ttl"]))


def source_rows(name: str, season: int = SEASON) -> list[dict]:
    """One source's stored scrapes: {key, data, fetched_at, ttl}."""
    t = source_cache
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(
            sa.select(t.c.key, t.c.data, t.c.fetched_at, t.c.ttl).where(t.c.season == season, t.c.name == name))]


def put_news_sources(rows: list[dict], season: int = SEASON) -> None:
    """Register the sources (key: season, id); a re-run refreshes kind/club/name/url/active and keeps the health columns."""
    if not rows:
        return
    vals = [{"season": season, **{k: r.get(k) for k in ("id", "kind", "club", "name", "url")},
             "active": bool(r.get("active", True))} for r in rows]
    with _write() as c:
        c.execute(_upsert(news_sources_table, vals, ["season", "id"], ["kind", "club", "name", "url", "active"]))


def news_sources(active_only: bool = True, season: int = SEASON) -> list[dict]:
    t = news_sources_table
    q = sa.select(t).where(t.c.season == season).order_by(t.c.id)
    if active_only:
        q = q.where(t.c.active.is_(True))
    with _read() as c:
        return [{k: v for k, v in r._mapping.items() if k != "season"} for r in c.execute(q)]


def mark_news_source(id: str, ok: bool, error: str | None, at: str, season: int = SEASON) -> None:
    """A fetch's result: ok clears the error run; a failure keeps the run's first time."""
    t = news_sources_table
    with _write() as c:
        where = (t.c.season == season, t.c.id == id)
        if ok:
            c.execute(sa.update(t).where(*where).values(ok_at=at, error=None, failing_since=None))
        else:
            c.execute(sa.update(t).where(*where).values(error=error, failing_since=sa.func.coalesce(t.c.failing_since, at)))


_ITEM_COLS = ("source_id", "ext_id", "url", "published_at", "fetched_at", "title", "text", "club", "at_s", "hash")


def add_news_items(rows: list[dict], season: int = SEASON) -> list[int]:
    """Store the items not already known (key: season, source_id, ext_id). Returns the new ids only."""
    if not rows:
        return []
    vals = [{"season": season, **{k: r.get(k) for k in _ITEM_COLS}, "player_ids": r.get("player_ids") or []} for r in rows]
    with _write() as c:
        stmt = _upsert(news_items_table, vals, ["season", "source_id", "ext_id"], None).returning(news_items_table.c.id)
        return [r[0] for r in c.execute(stmt).all()]


def news_item_hashes(source_id: str, ext_ids: list[str], season: int = SEASON) -> dict[str, tuple[int, str]]:
    """ext_id -> (item id, hash) for the ones already stored: the collectors' edit check."""
    if not ext_ids:
        return {}
    t = news_items_table
    q = sa.select(t.c.ext_id, t.c.id, t.c.hash).where(t.c.season == season, t.c.source_id == source_id, t.c.ext_id.in_(ext_ids))
    with _read() as c:
        return {r.ext_id: (r.id, r.hash) for r in c.execute(q)}


def notice_keys(prefix: str, season: int = SEASON, *, ref=None) -> list[str]:
    """Keys of the notices already pushed (sent_at set, outcome sent) that start with `prefix`."""
    t = notices
    q = (sa.select(t.c.key).where(t.c.league == _league_key(ref), t.c.season == season, t.c.sent_at.is_not(None),
                                  t.c.outcome == "sent", t.c.key.startswith(prefix, autoescape=True)).order_by(t.c.key))
    with _read() as c:
        return list(c.execute(q).scalars())


def notices_since(prefix: str, since: str, season: int = SEASON, *, ref=None) -> list[dict]:
    """{key, created_at, outcome} of the notices starting with `prefix` queued at or after `since`, any outcome (None
    = still waiting). Compared as text, so the caller passes a margin and re-checks the times."""
    t = notices
    q = (sa.select(t.c.key, t.c.created_at, t.c.outcome)
         .where(t.c.league == _league_key(ref), t.c.season == season, t.c.created_at >= since,
                t.c.key.startswith(prefix, autoescape=True)).order_by(t.c.key))
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def put_news_media(row: dict, season: int = SEASON) -> None:
    cols = ["source_id", "url", "title", "published_at", "segs", "fetched_at"]
    with _write() as c:
        c.execute(_upsert(news_media_table, [{"season": season, "id": row["id"], **{k: row[k] for k in cols}}],
                          ["season", "id"], cols))


def news_media(id: str, season: int = SEASON) -> dict | None:
    t = news_media_table
    with _read() as c:
        r = c.execute(sa.select(t).where(t.c.season == season, t.c.id == id)).first()
    return {k: v for k, v in r._mapping.items() if k != "season"} if r else None


def _item_out(r) -> dict:
    return {k: v for k, v in r._mapping.items() if k != "season"}


def recheck_items(since: str, season: int = SEASON) -> list[dict]:
    """Items published at or after `since` and not withdrawn: the window the collectors re-fetch for edits and deletions."""
    t = news_items_table
    q = (sa.select(t).where(t.c.season == season, t.c.published_at >= since, t.c.withdrawn_at.is_(None))
         .order_by(t.c.published_at, t.c.id))
    with _read() as c:
        return [_item_out(r) for r in c.execute(q)]


def update_item_text(item_id: int, title: str, text: str, hash: str, at: str, season: int = SEASON) -> None:
    """An edit: the earlier text goes to news_item_versions, and tagged_at clears so the item is tagged again."""
    t = news_items_table
    with _write() as c:
        old = c.execute(sa.select(t.c.hash, t.c.title, t.c.text).where(t.c.season == season, t.c.id == item_id)).first()
        if old is None:
            return
        c.execute(sa.insert(news_item_versions).values(season=season, item_id=item_id, hash=old.hash, title=old.title,
                                                       text=old.text, seen_at=at))
        c.execute(sa.update(t).where(t.c.season == season, t.c.id == item_id)
                  .values(title=title, text=text, hash=hash, tagged_at=None))


def _bump_story(c, story_id: int, season: int, **values) -> None:
    t = news_stories
    c.execute(sa.update(t).where(t.c.season == season, t.c.id == story_id).values(rev=t.c.rev + 1, **values))


def _relatest(c, story_id: int, season: int) -> None:
    """The story's latest_at from its newest live item; a story with none keeps its own."""
    t, s = news_items_table, news_stories
    at = c.execute(sa.select(sa.func.max(t.c.published_at))
                   .where(t.c.season == season, t.c.story_id == story_id, t.c.withdrawn_at.is_(None))).scalar()
    if at is not None:
        c.execute(sa.update(s).where(s.c.season == season, s.c.id == story_id).values(latest_at=at))


def withdraw_item(item_id: int, at: str, season: int = SEASON) -> dict | None:
    """Mark an item deleted or gone. When that leaves its story no live item, the story is retracted (rev+1, and it
    re-surfaces like set_story_status, at the retraction's time) and returned; otherwise None, its news time now the
    newest source left."""
    t, s = news_items_table, news_stories
    with _write() as c:
        c.execute(sa.update(t).where(t.c.season == season, t.c.id == item_id).values(withdrawn_at=at))
        sid = c.execute(sa.select(t.c.story_id).where(t.c.season == season, t.c.id == item_id)).scalar()
        if sid is None:
            return None
        live = c.execute(sa.select(sa.func.count()).select_from(t)
                         .where(t.c.season == season, t.c.story_id == sid, t.c.withdrawn_at.is_(None))).scalar()
        cur = c.execute(sa.select(s.c.status).where(s.c.season == season, s.c.id == sid)).scalar()
        if live or cur in (None, "retracted"):
            _relatest(c, sid, season)
            return None
        _bump_story(c, sid, season, status="retracted", status_at=at, updated_at=at, latest_at=at)
        return dict(c.execute(sa.select(s).where(s.c.id == sid)).first()._mapping)


def set_story_status(story_id: int, status: str, at: str, note: str | None, season: int = SEASON) -> None:
    """A correction or retraction: the story re-surfaces (rev+1, updated_at) so it is written and pushed again."""
    with _write() as c:
        _bump_story(c, story_id, season, status=status, status_at=at, status_note=note, updated_at=at)


def untagged_items(season: int = SEASON) -> list[dict]:
    """Newest first: in a backlog (a first run, Jev down a while) today's news is tagged before last week's."""
    t = news_items_table
    q = (sa.select(t).where(t.c.season == season, t.c.tagged_at.is_(None), t.c.withdrawn_at.is_(None))
         .order_by(t.c.published_at.desc(), t.c.id.desc()))
    with _read() as c:
        return [_item_out(r) for r in c.execute(q)]


def tag_item(item_id: int, player_ids: list[int], jev: dict | None, story_id: int | None, at: str,
             season: int = SEASON) -> None:
    t = news_items_table
    with _write() as c:
        c.execute(sa.update(t).where(t.c.season == season, t.c.id == item_id)
                  .values(player_ids=player_ids, jev=jev, story_id=story_id, tagged_at=at))
        if story_id is not None:
            _relatest(c, story_id, season)


def add_story(row: dict, season: int = SEASON) -> int:
    """`latest_at` starts at `first_at`; tagging its items into it moves it on."""
    cols = ("first_item_id", "first_at", "updated_at", "latest_at", "headline", "overview", "kind")
    vals = {"latest_at": row["first_at"], **{k: row[k] for k in cols if k in row}}
    with _write() as c:
        return c.execute(sa.insert(news_stories).values(season=season, **vals).returning(news_stories.c.id)).scalar()


def update_story(story_id: int, fields: dict, season: int = SEASON) -> None:
    if not fields:
        return
    with _write() as c:
        c.execute(sa.update(news_stories).where(news_stories.c.season == season, news_stories.c.id == story_id)
                  .values(**fields))


def _stories_out(c, rows, season: int) -> list[dict]:
    """Story rows dressed with their `players` (story_player rows) and `items` (item rows, oldest first)."""
    out = [{k: v for k, v in r._mapping.items() if k != "season"} for r in rows]
    ids = [s["id"] for s in out]
    if not ids:
        return out
    p, i = news_story_players, news_items_table
    players = c.execute(sa.select(p).where(p.c.season == season, p.c.story_id.in_(ids)).order_by(p.c.player_id))
    items = c.execute(sa.select(i).where(i.c.season == season, i.c.story_id.in_(ids)).order_by(i.c.published_at, i.c.id))
    by_p: dict[int, list] = {}
    by_i: dict[int, list] = {}
    for r in players:
        by_p.setdefault(r.story_id, []).append({k: v for k, v in r._mapping.items() if k != "season"})
    for r in items:
        by_i.setdefault(r.story_id, []).append(_item_out(r))
    for s in out:
        s["players"], s["items"] = by_p.get(s["id"], []), by_i.get(s["id"], [])
    return out


def story(story_id: int, season: int = SEASON) -> dict | None:
    t = news_stories
    with _read() as c:
        rows = c.execute(sa.select(t).where(t.c.season == season, t.c.id == story_id)).all()
        return _stories_out(c, rows, season)[0] if rows else None


def all_stories(season: int = SEASON) -> list[dict]:
    """Every story, dressed, in id order: the news bench's view of a replay (`newsdesk.bench`)."""
    t = news_stories
    with _read() as c:
        return _stories_out(c, c.execute(sa.select(t).where(t.c.season == season).order_by(t.c.id)).all(), season)


def recent_stories(player_ids: list[int], since: str, season: int = SEASON) -> list[dict]:
    """Stories naming any of these players and updated at or after `since`, newest first: what a new item may join,
    so never a retracted one (news after a withdrawn report is a story of its own)."""
    if not player_ids:
        return []
    t, p = news_stories, news_story_players
    named = sa.select(p.c.story_id).where(p.c.season == season, p.c.player_id.in_(player_ids))
    q = (sa.select(t).where(t.c.season == season, t.c.updated_at >= since, t.c.id.in_(named),
                            t.c.status != "retracted")
         .order_by(t.c.updated_at.desc(), t.c.id.desc()))
    with _read() as c:
        return _stories_out(c, c.execute(q).all(), season)


def player_stories(player_ids: list[int], since: str, season: int = SEASON) -> dict[int, list[dict]]:
    """Each player's stories with news at or after `since` (latest_at), newest first, ● ones too, never a retracted one:
    the outlook's fact sheet. A player with none is absent."""
    if not player_ids:
        return {}
    t, p = news_stories, news_story_players
    named = sa.select(p.c.story_id).where(p.c.season == season, p.c.player_id.in_(player_ids))
    q = (sa.select(t).where(t.c.season == season, t.c.latest_at >= since, t.c.id.in_(named), t.c.status != "retracted")
         .order_by(t.c.latest_at.desc(), t.c.id.desc()))
    with _read() as c:
        stories = _stories_out(c, c.execute(q).all(), season)
    out: dict[int, list[dict]] = {}
    want = set(player_ids)
    for s in stories:
        for r in s["players"]:
            if r["player_id"] in want:
                out.setdefault(r["player_id"], []).append(s)
    return out


def put_story_players(story_id: int, rows: list[dict], season: int = SEASON) -> None:
    """The model's call per player (key: season, story, player); the written reason and flag are left alone."""
    if not rows:
        return
    cols = ["effect", "kind", "prob", "confidence"]
    vals = [{"season": season, "story_id": story_id, "player_id": r["player_id"], **{k: r[k] for k in cols}} for r in rows]
    with _write() as c:
        c.execute(_upsert(news_story_players, vals, ["season", "story_id", "player_id"], cols))


def ask_reword(story_id: int, season: int = SEASON) -> None:
    """Jev found the written headline stale: the story is written again, at the same rev (so it does not push)."""
    t = news_stories
    with _write() as c:
        c.execute(sa.update(t).where(t.c.season == season, t.c.id == story_id).values(reword=t.c.reword + 1))


def unwritten_stories(limit: int | None = None, season: int = SEASON) -> list[dict]:
    """Stories whose rev moved past their words, or whose headline Jev found stale; the newest news first, so the
    freshest is written (and can push) before a backlog's."""
    t = news_stories
    q = (sa.select(t).where(t.c.season == season, sa.or_(t.c.written_rev < t.c.rev, t.c.reword > 0))
         .order_by(t.c.latest_at.desc(), t.c.id.desc()).limit(limit))
    with _read() as c:
        return _stories_out(c, c.execute(q).all(), season)


def put_story_words(story_id: int, rev: int, headline: str, overview: str, lines: dict[int, tuple[str, bool]],
                    reword: int = 0, season: int = SEASON) -> bool:
    """Store the written story, but only for the rev (and the headline refreshes, `reword`) it was written from: a
    story that moved on meanwhile is written again, and this stale text is dropped (False)."""
    t, p = news_stories, news_story_players
    with _write() as c:
        done = c.execute(sa.update(t).where(t.c.season == season, t.c.id == story_id, t.c.rev == rev,
                                            t.c.reword == reword)
                         .values(headline=headline, overview=overview, written_rev=rev, reword=0))
        if not done.rowcount:
            return False
        for pid, (reason, flag) in lines.items():
            c.execute(sa.update(p).where(p.c.season == season, p.c.story_id == story_id, p.c.player_id == pid)
                      .values(reason=reason, flag=flag))
        return True


def news_feed(before: str | None, limit: int, player_id: int | None = None, since: str | None = None,
              season: int = SEASON) -> list[dict]:
    """Written stories (retracted and corrected ones too, with their status), newest news first (latest_at: an
    update is its newest source, so it re-surfaces the story), strictly after the cursor `before` ("<latest_at>|<id>"
    in the (latest_at, id) order, or a bare timestamp, which is everything older); optionally only those naming a
    player as a subject (his row ▲ or ▼: a story that only names him, ●, is not his news), or with news at or after
    `since`."""
    t, p = news_stories, news_story_players
    q = sa.select(t).where(t.c.season == season, t.c.written_rev >= 1)
    if before:
        at, _, sid = before.partition("|")
        q = q.where(t.c.latest_at < at if not sid.isdigit()
                    else sa.tuple_(t.c.latest_at, t.c.id) < sa.tuple_(at, int(sid)))
    if since is not None:
        q = q.where(t.c.latest_at >= since)
    if player_id is not None:
        q = q.where(t.c.id.in_(sa.select(p.c.story_id).where(p.c.season == season, p.c.player_id == player_id,
                                                             p.c.effect != "none")))
    q = q.order_by(t.c.latest_at.desc(), t.c.id.desc()).limit(limit)
    with _read() as c:
        return _stories_out(c, c.execute(q).all(), season)


def _decision_out(r) -> dict:
    # league and season are implied by the read and import_key is the import's own dedupe key: none is part of a decision
    return {k: v for k, v in r._mapping.items() if k not in ("league", "season", "import_key")}


def add_decision(row: dict, season: int = SEASON, *, ref=None) -> int:
    league = _league_key(ref)
    with _write() as c:
        return c.execute(sa.insert(decisions_table).values({**row, "league": league, "season": season})
                         .returning(decisions_table.c.id)).scalar()


def put_week_claims(week: int, row: dict, season: int = SEASON, *, ref=None) -> dict:
    """The app claims row of the week's plan generation (facts.gen, the plans the waiver run retired before it): a
    resubmit replaces what was sent, keeping the rationale and outcome given since. A plan started after Wednesday's
    run is the same ESPN week but a new decision, so it gets its own row and never inherits the run's reason."""
    t = decisions_table
    keep = {"rationale", "rationale_note", "outcome", "outcome_at"}
    gen = (row.get("facts") or {}).get("gen", 0)
    league = _league_key(ref)
    with _write() as c:
        rows = c.execute(sa.select(t.c.id, t.c.rationale, t.c.facts).where(
            t.c.league == league, t.c.season == season, t.c.week == week, t.c.kind == "claims",
            t.c.source == "app")).all()
        old = next((r for r in rows if (r.facts or {}).get("gen", 0) == gen), None)
        if old is None:
            i = c.execute(sa.insert(t).values({**row, "league": league, "season": season, "week": week})
                          .returning(t.c.id)).scalar()
            return {"id": i, "needs_rationale": row.get("rationale") is None}
        c.execute(sa.update(t).where(t.c.id == old.id).values({k: v for k, v in row.items() if k not in keep}))
        return {"id": old.id, "needs_rationale": old.rationale is None}


def update_decision(id: int, fields: dict, season: int = SEASON, *, ref=None) -> dict | None:
    t, league = decisions_table, _league_key(ref)
    with _write() as c:
        if not c.execute(sa.update(t).where(t.c.league == league, t.c.season == season, t.c.id == id)
                         .values(fields)).rowcount:
            return None
        return _decision_out(c.execute(sa.select(t).where(t.c.id == id)).one())


def decision(id: int, season: int = SEASON, *, ref=None) -> dict | None:
    t = decisions_table
    q = sa.select(t).where(t.c.league == _league_key(ref), t.c.season == season, t.c.id == id)
    with _read() as c:
        r = c.execute(q).first()
    return _decision_out(r) if r else None


def decisions(season: int = SEASON, *, ref=None) -> list[dict]:
    t = decisions_table
    q = sa.select(t).where(t.c.league == _league_key(ref), t.c.season == season).order_by(t.c.at.desc(), t.c.id.desc())
    with _read() as c:
        return [_decision_out(r) for r in c.execute(q)]


# the natural key of each table the file import writes (ON CONFLICT DO NOTHING on it); sigma_fits stays global
_KEYS = {"transactions": ["league", "season", "id"], "draft_picks": ["league", "season", "overall"],
         "results": ["league", "season", "week"], "plays": ["league", "season", "week", "player_id", "at", "total"],
         "outlooks": ["league", "season", "week", "kind", "player_id"], "sigma_fits": ["season", "fitted_at"],
         "claim_plans": ["league", "season", "week"], "decisions": ["league", "season", "import_key"]}


def insert_new(table_name: str, rows: list[dict]) -> int:
    """Insert the rows not already present (DO NOTHING on the table's key); returns how many were new.
    Rows carry their own season and league. For the file import, which must never overwrite."""
    if not rows:
        return 0
    t = metadata.tables[table_name]
    pk = list(t.primary_key.columns)[0]
    with _write() as c:  # batches: one multi-row VALUES would pass SQLite's bind-variable limit on a big table
        return sum(len(c.execute(_upsert(t, rows[i:i + 500], _KEYS[table_name], None).returning(pk)).all())
                   for i in range(0, len(rows), 500))


def count(table_name: str, season: int = SEASON, *, ref=None) -> int:
    """The season's rows in a table; in one league (`ref`, None the home one) when the table has the column."""
    t = metadata.tables[table_name]
    q = sa.select(sa.func.count()).select_from(t).where(t.c.season == season)
    if "league" in t.c:
        q = q.where(t.c.league == _league_key(ref))
    with _read() as c:
        return c.execute(q).scalar()


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "import":
        from . import db_import
        from .config import JOURNAL
        return db_import.main(JOURNAL, DATA, SEASON)
    if cmd == "backup":
        from . import db_backup
        return db_backup.main(argv[1:])
    print("usage: python -m ffl.db import | backup [dest_dir]")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
