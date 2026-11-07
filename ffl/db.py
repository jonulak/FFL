"""The season's shared state in one SQL database (SQLAlchemy Core; Alembic owns the schema).

The only module that talks SQL. Every function takes and returns plain dicts, so season.py stays
pure. Postgres, at FFL_DB_URL (`postgresql+psycopg://...`; required: there is no default database).
Writes are one short transaction each.

    uv run alembic upgrade head          # create / migrate the schema
    uv run python -m ffl.db import       # one-time: the old journal/ and data/ files in (idempotent)
    uv run python -m ffl.db backup DIR   # a checked snapshot into DIR (else FFL_BACKUP_DEST; the stack's cron nightly)
    uv run python -m ffl.db restore S --url U --yes   # a dump (file, or dir: the newest) back in; without --yes it names the target and stops
    uv run python -m ffl.db pgcopy F     # the SQLite file F (schema 0024, read-only) into FFL_DB_URL, checked
    uv run python -m ffl.db counts A [B] # rows per table, two databases side by side (`env`: FFL_DB_URL), read-only
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
from contextlib import contextmanager

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import distinct_on, insert as pg_insert

from .config import DATA, ROOT, SEASON

log = logging.getLogger(__name__)

metadata = sa.MetaData()


class SchemaError(RuntimeError):
    pass


DBError = sa.exc.SQLAlchemyError  # callers outside db.py catch DB errors through this
IntegrityError = sa.exc.IntegrityError  # a unique key taken (a DBError too)


class Taken(DBError):
    """A username or email another user already has."""


def _season():
    return sa.Column("season", sa.Integer, nullable=False, default=SEASON)


def _league():
    """The league a row belongs to, a registry key ("espn:100000001"); the phase-1b tables carry it."""
    return sa.Column("league", sa.String, nullable=False)


def _user():
    """The user a personal row is (ffl-soc.6.2.3); deleting the user deletes it."""
    return sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


def _team():
    """The fantasy team a row is, in its league: what ESPN keeps per team, which co-managers share."""
    return sa.Column("team_id", sa.Integer, nullable=False)


leagues_table = sa.Table(
    "leagues", metadata,
    sa.Column("key", sa.String, primary_key=True),       # "<provider>:<provider league id>"
    sa.Column("provider", sa.String, nullable=False),
    sa.Column("provider_id", sa.String, nullable=False),
    sa.Column("season", sa.Integer, nullable=False),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("lineup_format", sa.String),
    sa.Column("strategy", sa.String),                    # strategies.key; the league plays its latest version
    sa.Column("added_at", sa.String, nullable=False),
)

# Accounts: a user (username + password; 0036) and their sessions. Times are UTC ISO strings.
users = sa.Table(
    "users", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("email", sa.String),
    sa.Column("name", sa.String),
    sa.Column("created_at", sa.String, nullable=False),
    sa.Column("plan", sa.String, nullable=False, server_default="free"),
    sa.Column("plan_until", sa.String),
    sa.Column("username", sa.String),                    # NULL for an invite not yet set up
    sa.Column("password_hash", sa.String),
    sa.Column("is_admin", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("setup_token_hash", sa.String),            # sha256 hex of a setup link's token
    sa.Column("setup_expires", sa.String),
)
# One user per email, and per username, whatever its case (NULLs never collide).
sa.Index("uq_users_email_lower", sa.func.lower(users.c.email), unique=True)
sa.Index("uq_users_username_lower", sa.func.lower(users.c.username), unique=True)
sessions = sa.Table(
    "sessions", metadata,
    sa.Column("token_hash", sa.String, primary_key=True),  # sha256 hex of the cookie's token; the token is never stored
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("created_at", sa.String, nullable=False),
    sa.Column("last_seen", sa.String, nullable=False),
    sa.Column("expires_at", sa.String, nullable=False),
    sa.Column("ua", sa.String),
)
# A user's place in a league (ffl-soc.6.2.2): their team, their default league, their name for it, their strategy.
# A league's owner is its member with the lowest user id: the jobs and the CLI act through that membership until
# 6.2.3 splits personal work per member.
memberships = sa.Table(
    "memberships", metadata,
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("league", sa.String, sa.ForeignKey("leagues.key", ondelete="CASCADE"), nullable=False),
    sa.Column("team_id", sa.Integer),                    # NULL until known
    sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("name", sa.String),                        # the member's own name for the league; NULL = the league's
    sa.Column("strategy", sa.String),                    # strategies.key; NULL = the league's
    sa.Column("created_at", sa.String, nullable=False),
    sa.Column("espn_account", sa.String),                # the ESPN account's swid_hash (#460); NULL = the user's first
    sa.PrimaryKeyConstraint("user_id", "league"),
)
sa.Index("uq_memberships_default", memberships.c.user_id, unique=True,
         postgresql_where=memberships.c.is_default)

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
    sa.Column("seq", sa.BigInteger, sa.Identity(), nullable=False),  # insert order: the log is read in it
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

# Every `claude -p` call (ffl/claude_cli.py, #64): what it cost, by kind (outlook / news), model and league. `at` is
# UTC ISO, so its first ten characters are the day.
llm_calls = sa.Table(
    "llm_calls", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("at", sa.String, nullable=False),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("model", sa.String, nullable=False),
    sa.Column("league", sa.String),
    sa.Column("player_id", sa.Integer),
    sa.Column("input", sa.Integer, nullable=False),          # every token read, the cache's too
    sa.Column("output", sa.Integer, nullable=False),
    sa.Column("cost_usd", sa.Float, nullable=False),
    sa.Column("ok", sa.Boolean, nullable=False),
)
sa.Index("ix_llm_calls_at", llm_calls.c.at)
sa.Index("ix_llm_calls_kind", llm_calls.c.kind)

# The app's own secrets (`claude_token`), each value sealed by ffl/vault.py with its key as associated data.
app_secrets = sa.Table(
    "app_secrets", metadata,
    sa.Column("key", sa.Text, primary_key=True),
    sa.Column("value", sa.Text, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
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
    _league(),
    _season(),
    sa.Column("play_id", sa.String, nullable=False),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("body", sa.JSON, nullable=False),         # a play-feed row, match "called_back"
    sa.PrimaryKeyConstraint("league", "season", "play_id", "player_id"),
)
sa.Index("ix_called_back_week", called_back.c.league, called_back.c.season, called_back.c.week)

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
    sa.Column("settled", sa.Boolean, nullable=False, default=False, server_default=sa.false()),
    sa.Column("final", sa.Boolean, nullable=False, default=False, server_default=sa.false()),
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
    "outlooks", metadata, _season(),                     # shared by every league: one card per player and week
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("entry", sa.JSON, nullable=False),         # hash, card_hash, bullets, generated_at
    sa.Column("updated", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("season", "week", "player_id"),
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

# A team's waiver claim plan per week: the team's, as ESPN keeps claims per team (co-managers share it).
claim_plans = sa.Table(
    "claim_plans", metadata, _league(), _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("plan", sa.JSON, nullable=False),
    sa.Column("updated", sa.String, nullable=False),
    _team(),
    sa.PrimaryKeyConstraint("league", "season", "team_id", "week"),
)

# The players sat by hand for a week (the Roster sheet's toggle): left out of the lineup call. A row is a sit and
# the toggle off deletes it, so a new week starts with none.
sits = sa.Table(
    "sits", metadata, _league(), _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("at", sa.String, nullable=False),
    _user(),
    sa.PrimaryKeyConstraint("user_id", "league", "season", "week", "player_id"),
)

# The players on the watchlist (the Players page's eye toggle). A row is a watch; unwatching deletes it.
watchlist = sa.Table(
    "watchlist", metadata, _league(), _season(),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("added_at", sa.String, nullable=False),
    _user(),
    sa.PrimaryKeyConstraint("user_id", "league", "season", "player_id"),
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
    _user(),
    sa.PrimaryKeyConstraint("user_id", "league", "season", "key"),
    sa.Index("ix_watch_signals_player", "user_id", "league", "season", "player_id"),
)

# Which row of a starting slot each player stands in (season.reseat): ESPN's two WR slots are one slot to it and it
# lists them in roster-acquisition order, so a swap would jump rows. A player stands in `seat_id`'s place in that
# order; one with no row stands in his own.
seats = sa.Table(
    "seats", metadata, _league(), _season(),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("seat_id", sa.Integer, nullable=False),
    _team(),
    sa.PrimaryKeyConstraint("league", "season", "team_id", "player_id"),
)

# Web Push: one row per installed device (the browser's PushSubscription), the user who subscribed it last. A 404/410
# from the push service deletes it.
push_subscriptions = sa.Table(
    "push_subscriptions", metadata,
    sa.Column("endpoint", sa.String, primary_key=True),
    sa.Column("keys", sa.String, nullable=False),        # {p256dh, auth}, sealed (ffl/vault.py, `_push_aad`)
    sa.Column("ua", sa.String, nullable=False, default=""),
    sa.Column("created_at", sa.String, nullable=False),
    _user(),
    sa.Index("ix_push_subscriptions_user", "user_id"),
)

# A user's ESPN cookies (ffl-soc.3), one row per ESPN account (#460: a user may connect several): {espn_s2, swid}
# sealed as one token (ffl/vault.py, `_cred_aad`), never readable from a row or a dump without FFL_SECRET_KEYS.
# `swid_hash` names the account (`swid_hash()`; NULL on a row from before 0033 until it is first opened).
# `failing_since` is set when ESPN refuses them (401/403), cleared by a read they pass or by new cookies.
espn_credentials = sa.Table(
    "espn_credentials", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("swid_hash", sa.String),
    sa.Column("secret", sa.String, nullable=False),
    sa.Column("updated_at", sa.String, nullable=False),
    sa.Column("last_ok", sa.String),
    sa.Column("failing_since", sa.String),
    sa.Column("failing_reason", sa.String),
    sa.UniqueConstraint("user_id", "swid_hash", name="uq_espn_credentials_account"),
    sa.Index("ix_espn_credentials_user", "user_id"),
)

# Pairing the browser extension to an account (ffl-soc.6.3.3, ffl/espn_connect.py): the extension starts one and shows
# its code, the signed-in user types the code (confirmed: user_id set), the extension's poll then turns it into an
# extension_devices row and the pairing is deleted. Only hashes are stored.
espn_pairings = sa.Table(
    "espn_pairings", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("code_hash", sa.String, nullable=False, unique=True),
    sa.Column("poll_hash", sa.String, nullable=False, unique=True),
    sa.Column("label", sa.String, nullable=False),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE")),  # NULL until confirmed
    sa.Column("created_at", sa.String, nullable=False),
    sa.Column("expires_at", sa.String, nullable=False),
    sa.Column("confirmed_at", sa.String),
)
# A paired extension: its token can do one thing, send the user's ESPN cookies (POST /espn/cookies). Revoking deletes it.
extension_devices = sa.Table(
    "extension_devices", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("token_hash", sa.String, nullable=False, unique=True),
    _user(),
    sa.Column("label", sa.String, nullable=False),
    sa.Column("created_at", sa.String, nullable=False),
    sa.Column("last_used_at", sa.String),
    sa.Index("ix_extension_devices_user", "user_id"),
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
    _user(),                                             # whose devices it goes to
    sa.Column("read_at", sa.String),                     # read in the in-app list, or its push tapped
    sa.Column("deleted_at", sa.String),                  # a soft delete: the row is what stops a detector re-queueing it
    sa.PrimaryKeyConstraint("user_id", "league", "season", "key"),
)
sa.Index("ix_notices_unsent", notices.c.user_id, notices.c.sent_at)

# notify's GLOBAL memory, one JSON value per key; a league's own keys (LEAGUE_STATE_KEYS) live in league_state, a
# user's, a member's and a team's in user_state, member_state and team_state (below).
notify_state = sa.Table(
    "notify_state", metadata, _season(),
    sa.Column("key", sa.String, nullable=False),
    sa.Column("value", sa.JSON, nullable=False),
    sa.PrimaryKeyConstraint("season", "key"),
)

# The state keys that belong to one league (what a waiver run or a claim did is that league's), kept in league_state.
LEAGUE_STATE_KEYS = frozenset({"pregame_pulls", "final_plays_done", "news_to_notify", "espn_week"})
league_state = sa.Table(
    "league_state", metadata, _league(), _season(),
    sa.Column("key", sa.String, nullable=False),
    sa.Column("value", sa.JSON, nullable=False),
    sa.PrimaryKeyConstraint("league", "season", "key"),
)

# Personal state (ffl-soc.6.2.3): a user's in every league (quiet hours, news mutes, Players views), a member's in one
# league (push and decision switches, the watch pass's memory), and what ESPN keeps per team (the claims pending at
# the last tick and the waiver run they were read against: co-managers share them).
USER_STATE_KEYS = frozenset({"prefs", "news_mutes", "player_presets"})
MEMBER_STATE_KEYS = frozenset({"notify_prefs", "decision_prefs", "watch_state"})
TEAM_STATE_KEYS = frozenset({"pending_claims", "waiver_run"})
user_state = sa.Table(
    "user_state", metadata, _user(), _season(),
    sa.Column("key", sa.String, nullable=False),
    sa.Column("value", sa.JSON, nullable=False),
    sa.PrimaryKeyConstraint("user_id", "season", "key"),
)
member_state = sa.Table(
    "member_state", metadata,
    sa.Column("user_id", sa.Integer, nullable=False), _league(), _season(),
    sa.Column("key", sa.String, nullable=False),
    sa.Column("value", sa.JSON, nullable=False),
    sa.ForeignKeyConstraint(["user_id", "league"], ["memberships.user_id", "memberships.league"], ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("user_id", "league", "season", "key"),
)
team_state = sa.Table(
    "team_state", metadata, _league(), _season(), _team(),
    sa.Column("key", sa.String, nullable=False),
    sa.Column("value", sa.JSON, nullable=False),
    sa.PrimaryKeyConstraint("league", "season", "team_id", "key"),
)
# A member's roster caps (`ffl.leagues caps`, Settings): never above the league's (league_caps.provider_limit).
member_caps = sa.Table(
    "member_caps", metadata,
    sa.Column("user_id", sa.Integer, nullable=False), _league(),
    sa.Column("pos", sa.String, nullable=False),
    sa.Column("my_cap", sa.Integer, nullable=False),
    sa.ForeignKeyConstraint(["user_id", "league"], ["memberships.user_id", "memberships.league"], ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("user_id", "league", "pos"),
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
    _user(),
    sa.UniqueConstraint("league", "season", "import_key", name="uq_decisions_import"),
    sa.Index("ix_decisions_user", "user_id", "league", "season"),
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
    # whose pages (ffl-soc.6.2.2: a page is never served to another member); 0 = no user (a registry without one)
    sa.Column("user_id", sa.Integer, nullable=False, server_default="0"),
    sa.PrimaryKeyConstraint("user_id", "league", "season", "key"),
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

# An item's latest text in pieces, a vector each (the embed stage): what GET /news/search matches a source's detail on.
news_item_chunks = sa.Table(
    "news_item_chunks", metadata, _season(),
    sa.Column("item_id", sa.Integer, nullable=False),
    sa.Column("pos", sa.Integer, nullable=False),
    sa.Column("hash", sa.String, nullable=False),        # the item's hash when chunked: another = revised, chunk again
    sa.Column("text", sa.String, nullable=False),
    sa.Column("embedding", Vector(384), nullable=False),
    sa.PrimaryKeyConstraint("season", "item_id", "pos"),
)

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
    sa.Column("updated_at", sa.String, nullable=False),  # when the app last moved it (a rev, a status): the tagger's window
    sa.Column("latest_at", sa.String, nullable=False),   # its newest live source's published_at: the feed's order
    sa.Column("rev", sa.Integer, nullable=False, default=1),
    sa.Column("written_rev", sa.Integer, nullable=False, default=0),   # < rev = the words are stale or not yet written
    # headline refreshes asked since the words were written (the tagger: the headline no longer sums the story up). A refresh
    # is rewritten but never bumps rev, so it never pushes
    sa.Column("reword", sa.Integer, nullable=False, default=0, server_default=sa.text("0")),
    sa.Column("headline", sa.String, nullable=False, default=""),
    sa.Column("overview", sa.String, nullable=False, default=""),
    sa.Column("kind", sa.String, nullable=False, default=""),
    sa.Column("status", sa.String, nullable=False, default="live"),    # live|corrected|retracted
    sa.Column("status_at", sa.String),
    sa.Column("status_note", sa.String),                 # which source corrected or withdrew it
    sa.Column("embedding", Vector(384)),                 # its written words (ffl.embed); NULL until the embed stage
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
    u = os.environ.get("FFL_DB_URL")
    if not u:
        raise SchemaError("FFL_DB_URL is not set: point it at the Postgres database "
                          "(postgresql+psycopg://...), in .env or the environment")
    return _check_url(u, "FFL_DB_URL")


def _check_url(u: str, what: str) -> str:
    if not u.startswith("postgresql+psycopg://"):
        raise SchemaError(f"{what} must be a postgresql+psycopg:// URL (Postgres is the only database), "
                          f"not {u.split(':', 1)[0]}:")
    return u


def _finite(o):
    """`o` with every NaN / ±Infinity float made None, through dicts (order kept) and lists: Postgres's json refuses
    them, where SQLite stored them."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return o


def _json_dumps(o) -> str:
    """The engine's JSON writer: json.dumps as SQLAlchemy's default, NaN and ±Infinity written as null."""
    return json.dumps(_finite(o))


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
    # a small pool: the RDS micro's connection cap is shared with Cantrip; pre-ping drops a connection the server closed
    return sa.create_engine(url(), pool_pre_ping=True, pool_size=5, max_overflow=5, json_serializer=_json_dumps)


def engine() -> sa.Engine:
    """The process's engine; the first call checks the schema is at Alembic's head."""
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:  # concurrent API threads at boot must not build (and leak) two engines
            if _ENGINE is None:
                _ENGINE = _build_checked()
    return _ENGINE


def _build_checked() -> sa.Engine:
    eng = _make_engine()
    from alembic.runtime.migration import MigrationContext
    with eng.connect() as c:
        current = MigrationContext.configure(c).get_current_revision()
    if current != _head():
        eng.dispose()
        raise SchemaError(f"{eng.url!r} is at schema {current!r}, code wants {_head()!r}: "
                          "run: uv run alembic upgrade head")
    return eng


def reset() -> None:
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is not None:
            _ENGINE.dispose()
        _ENGINE = None


@contextmanager
def _write():
    with engine().connect() as c, c.begin():
        yield c


@contextmanager
def _read():
    with engine().connect() as c:
        yield c


def _upsert(table: sa.Table, rows: list[dict], keys: list[str], update: list[str] | None):
    # Postgres refuses one statement that names a conflict key twice (CardinalityViolation; SQLite let the last
    # row win): keep one row per key, the last, at the key's first-seen position.
    rows = list({tuple(r.get(k) for k in keys): r for r in rows}.values())
    stmt = pg_insert(table).values(rows)
    if update is None:
        return stmt.on_conflict_do_nothing(index_elements=keys)
    return stmt.on_conflict_do_update(index_elements=keys, set_={c: stmt.excluded[c] for c in update})


def league_rows() -> list[dict]:
    """Every registered league, oldest first."""
    t = leagues_table
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(sa.select(t).order_by(t.c.added_at, t.c.key))]


def member_rows(user_id: int | None = None, *, everyone: bool = False) -> list[dict]:
    """Leagues as a member sees them: the leagues row with the membership's team, default, name and strategy laid
    over it (NULL keeps the league's), plus `user_id`, `is_default` and `owner` (the member is the league's lowest
    user id). `user_id` None: every league through its owner's membership (none: no team, user_id None), oldest
    first; a user: their memberships only, their default first; `everyone`: every membership, by league (oldest
    first) then user."""
    t, m = leagues_table, memberships
    first = sa.select(m.c.league, sa.func.min(m.c.user_id).label("owner_id")).group_by(m.c.league).subquery()
    cols = [t.c.key, t.c.provider, t.c.provider_id, t.c.season, t.c.lineup_format,
            sa.func.coalesce(m.c.name, t.c.name).label("name"),
            m.c.team_id, m.c.espn_account,
            sa.func.coalesce(m.c.strategy, t.c.strategy).label("strategy"),
            m.c.user_id, sa.func.coalesce(m.c.is_default, sa.false()).label("is_default"),
            (m.c.user_id == first.c.owner_id).label("owner")]
    q = sa.select(*cols).select_from(t.outerjoin(first, first.c.league == t.c.key))
    if everyone:
        q = q.join(m, m.c.league == t.c.key).order_by(t.c.added_at, t.c.key, m.c.user_id)
    elif user_id is None:
        q = q.outerjoin(m, sa.and_(m.c.league == t.c.key, m.c.user_id == first.c.owner_id)) \
            .order_by(t.c.added_at, t.c.key)
    else:
        q = q.join(m, sa.and_(m.c.league == t.c.key, m.c.user_id == user_id)) \
            .order_by(m.c.is_default.desc(), t.c.added_at, t.c.key)
    with _read() as c:
        return [{**r._mapping, "owner": bool(r.owner)} for r in c.execute(q)]


def put_membership(user_id: int, league: str, **values) -> None:
    """Upsert (user, league) with `values` (team_id, is_default, name, strategy, espn_account); is_default True clears the user's
    other default in the same transaction."""
    row = {"user_id": user_id, "league": league, "created_at": _iso(_utc_now()), **values}
    with _write() as c:
        if values.get("is_default"):
            c.execute(sa.update(memberships).where(memberships.c.user_id == user_id, memberships.c.league != league)
                      .values(is_default=False))
        c.execute(_upsert(memberships, [row], ["user_id", "league"], list(values) or None))


def drop_memberships(user_id: int, leagues: list[str]) -> None:
    """The user leaves these leagues (#460: their ESPN login for them was disconnected): the memberships (and with them
    member_state, member_caps) and the user's page_cache rows for them. Their own records there (decisions, the
    watchlist) stay and come back if they join again. The leagues stay registered."""
    if not leagues:
        return
    with _write() as c:
        c.execute(sa.delete(memberships).where(memberships.c.user_id == user_id, memberships.c.league.in_(leagues)))
        c.execute(sa.delete(page_cache).where(page_cache.c.user_id == user_id, page_cache.c.league.in_(leagues)))


def put_league(row: dict) -> None:
    """Upsert a league by key (added_at set on first insert only)."""
    with _write() as c:
        _put_league(c, row)


def _put_league(c, row: dict) -> None:
    from datetime import datetime
    t = leagues_table
    row = {**row, "added_at": datetime.now().astimezone().isoformat(timespec="seconds")}
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
        caps = {r.pos: {"provider_limit": r.provider_limit}
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


def caps_rows(*, ref) -> dict[str, dict]:
    """{pos: {provider_limit, my_cap}}: the league's limits and the member's own caps (a league seen through no
    member: its limits alone); a position in neither is left out (empty when nothing is stored)."""
    t, m, league = league_caps, member_caps, ref.key
    with _read() as c:
        out = {r.pos: {"provider_limit": r.provider_limit, "my_cap": None}
               for r in c.execute(sa.select(t).where(t.c.league == league))}
        mine = [] if ref.user_id is None else c.execute(sa.select(m).where(m.c.user_id == ref.user_id,
                                                                           m.c.league == league))
        for r in mine:
            out.setdefault(r.pos, {"provider_limit": None})["my_cap"] = r.my_cap
    return out


def put_my_cap(pos: str, my_cap: int, *, ref) -> None:
    """The member's cap at a position."""
    user, league = _member(ref)
    with _write() as c:
        c.execute(_upsert(member_caps, [{"user_id": user, "league": league, "pos": pos, "my_cap": my_cap}],
                          ["user_id", "league", "pos"], ["my_cap"]))


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


def seed_my_caps(caps: dict[str, int], *, ref) -> None:
    """The member's cap at each position that has none yet, in one statement (a set cap is never touched)."""
    if not caps:
        return
    user, league = _member(ref)
    with _write() as c:
        c.execute(_upsert(member_caps, [{"user_id": user, "league": league, "pos": p, "my_cap": n}
                                        for p, n in caps.items()], ["user_id", "league", "pos"], None))


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
    return ref.key


def _member(ref) -> tuple[int, str]:
    """(user id, league key) of the member `ref` is the view of: whose personal rows a read or write touches. A league
    seen through no member has none (ValueError: a caller that reaches here without a member is a bug)."""
    if ref.user_id is None:
        raise ValueError(f"{ref.key}: personal rows need a member's view of the league")
    return ref.user_id, ref.key


def _team_of(ref) -> tuple[str, int]:
    """(league key, team id) of the member's team: what ESPN keeps per team. ValueError when the membership has no
    team."""
    if ref.team_id is None:
        raise ValueError(f"{ref.key}: no team on this membership")
    return ref.key, ref.team_id


def add_plays(rows: list[dict], season: int = SEASON, *, ref) -> int:
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


def plays_rows(week: int, season: int = SEASON, *, ref) -> list[dict]:
    """One week's play-feed rows, oldest first."""
    t = plays
    q = sa.select(t.c.body).where(t.c.league == _league_key(ref), t.c.season == season, t.c.week == week) \
        .order_by(t.c.id)
    with _read() as c:
        return list(c.execute(q).scalars())


def update_plays(rows: list[dict], season: int = SEASON, *, ref) -> None:
    """Rewrite stored play-feed rows' bodies in place, each found by its key (week, player, at, total)."""
    t, league = plays, _league_key(ref)
    with _write() as c:
        for r in rows:
            c.execute(t.update().where(t.c.league == league, t.c.season == season, t.c.week == r["week"],
                                       t.c.player_id == r["player_id"], t.c.at == r["at"], t.c.total == r["total"])
                      .values(body=r))


CHUNK = 400  # rows per multi-row statement: a statement's bound parameters are capped (Postgres: 65535)


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


def put_called_back(put: list[dict], drop: list[tuple[str, int]], season: int = SEASON, *, ref) -> None:
    """Write a league's called-back rows (by play and player) and delete its (play_id, player_id) pairs in `drop`."""
    t, league = called_back, _league_key(ref)
    vals = [{"league": league, "season": season, "play_id": r["play_id"], "player_id": r["player_id"],
             "week": r["week"], "body": r} for r in put]
    with _write() as c:
        for i in range(0, len(vals), CHUNK):
            c.execute(_upsert(t, vals[i:i + CHUNK], ["league", "season", "play_id", "player_id"], ["week", "body"]))
        for play_id, player_id in drop:
            c.execute(t.delete().where(t.c.league == league, t.c.season == season, t.c.play_id == play_id,
                                       t.c.player_id == player_id))


def called_back_rows(week: int, season: int = SEASON, *, ref) -> list[dict]:
    t = called_back
    q = sa.select(t.c.body).where(t.c.league == _league_key(ref), t.c.season == season, t.c.week == week)
    with _read() as c:
        return list(c.execute(q).scalars())


_CORR_COLS = ("week", "player_id", "team_id", "old", "new", "at")


def _corr_vals(rows, season: int, league: str) -> list[dict]:
    return [{"league": league, "season": season, **{k: r.get(k) for k in _CORR_COLS}} for r in rows]


def box_doc(week: int, season: int = SEASON, *, ref) -> dict | None:
    """One week's stored box-score document: {doc, hash, fetched_at, settled, final}, or None."""
    t = box_scores_table
    q = sa.select(t.c.doc, t.c.hash, t.c.fetched_at, t.c.settled, t.c.final).where(
        t.c.league == _league_key(ref), t.c.season == season, t.c.week == week)
    with _read() as c:
        r = c.execute(q).first()
    return dict(r._mapping) if r else None


def box_weeks(season: int = SEASON, *, ref) -> dict[int, dict]:
    """Every stored week -> {fetched_at, settled, final}, without the documents."""
    t = box_scores_table
    q = sa.select(t.c.week, t.c.fetched_at, t.c.settled, t.c.final).where(t.c.league == _league_key(ref),
                                                                          t.c.season == season)
    with _read() as c:
        return {r.week: {"fetched_at": r.fetched_at, "settled": r.settled, "final": r.final} for r in c.execute(q)}


def put_box_doc(week: int, doc: dict, hash: str, fetched_at: float, settled: bool | None = None,
                corrections=(), season: int = SEASON, final: bool = False, *, ref) -> bool:
    """Store a read of one week's box scores in one transaction: insert the row if absent; else always set
    fetched_at (the correction pass's throttle and the current week's 60 s hit read it) and final (the read
    decides: made after the week finished or not), doc and hash only when the hash differs, settled when not
    None. `corrections` go into box_score_corrections in the same transaction. Returns True when doc/hash
    were (re)written."""
    t, league = box_scores_table, _league_key(ref)
    key = (t.c.league == league, t.c.season == season, t.c.week == week)
    with _write() as c:
        # insert-if-absent, then lock: two first reads of a week must not both INSERT
        made = c.execute(_upsert(t, [{"league": league, "season": season, "week": week, "doc": doc, "hash": hash,
                                      "fetched_at": fetched_at, "settled": bool(settled), "final": final}],
                                 ["league", "season", "week"], None).returning(t.c.week)).first()
        if made is not None:
            wrote = True
        else:
            old = c.execute(sa.select(t.c.hash).where(*key).with_for_update()).one()
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


def expire_live_boxes(season: int = SEASON, *, ref) -> None:
    """fetched_at = 0 on every non-final row: after a league move no stored current week counts as a 60 s hit.
    A final row keeps its fetched_at, the correction pass's hourly throttle."""
    t, league = box_scores_table, _league_key(ref)
    with _write() as c:
        c.execute(t.update().where(t.c.league == league, t.c.season == season, t.c.final.is_(False))
                  .values(fetched_at=0.0))


def gap_rows(week: int, ids, season: int = SEASON, *, ref) -> dict[int, list[dict]]:
    """The stored gap-week game rows of those players for a week (only the ones stored)."""
    ids = list(ids)
    if not ids:
        return {}
    t = gap_games_table
    q = sa.select(t.c.player_id, t.c.rows).where(t.c.league == _league_key(ref), t.c.season == season,
                                                 t.c.week == week, t.c.player_id.in_(ids))
    with _read() as c:
        return {r.player_id: r.rows for r in c.execute(q)}


def gap_ids(week: int, season: int = SEASON, *, ref) -> list[int]:
    t = gap_games_table
    q = sa.select(t.c.player_id).where(t.c.league == _league_key(ref), t.c.season == season, t.c.week == week) \
        .order_by(t.c.player_id)
    with _read() as c:
        return list(c.execute(q).scalars())


def put_gap_rows(week: int, rows: dict[int, list[dict]], fetched_at: float, season: int = SEASON, *, ref) -> None:
    """Upsert (league, season, week, player_id) -> rows, fetched_at."""
    if not rows:
        return
    league = _league_key(ref)
    vals = [{"league": league, "season": season, "week": week, "player_id": pid, "rows": r, "fetched_at": fetched_at}
            for pid, r in rows.items()]
    with _write() as c:
        c.execute(_upsert(gap_games_table, vals, ["league", "season", "week", "player_id"], ["rows", "fetched_at"]))


def add_box_corrections(rows: list[dict], season: int = SEASON, *, ref) -> None:
    """Log gap-week corrections (team_id None)."""
    if not rows:
        return
    league = _league_key(ref)
    with _write() as c:
        c.execute(box_score_corrections.insert(), _corr_vals(rows, season, league))


def box_corrections(season: int = SEASON, week: int | None = None, *, ref) -> list[dict]:
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


def add_transactions(rows: list[dict], season: int = SEASON, *, ref) -> list[dict]:
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


def transactions(season: int = SEASON, *, ref) -> list[dict]:
    t = transactions_table
    # insert order (the CSV's row order, then each sync's): a waiver run stamps every claim with one date
    q = sa.select(*[t.c[k] for k in _TX_COLS]).where(t.c.league == _league_key(ref), t.c.season == season) \
        .order_by(t.c.seq)
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


_RESULT_COLS = ("week", "opponent", "my_score", "opp_score", "result")


def put_results(rows: list[dict], season: int = SEASON, *, ref) -> None:
    """Upsert per week. A stale League that knows fewer weeks only rewrites the ones it has."""
    if rows:
        league = _league_key(ref)
        with _write() as c:
            c.execute(_upsert(results, [{"league": league, "season": season, **r} for r in rows], _KEYS["results"],
                              ["opponent", "my_score", "opp_score", "result"]))


def results_rows(season: int = SEASON, *, ref) -> list[dict]:
    t = results
    q = sa.select(*[t.c[k] for k in _RESULT_COLS]).where(t.c.league == _league_key(ref), t.c.season == season) \
        .order_by(t.c.week)
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


_PICK_COLS = ("overall", "round", "pick", "team_id", "team", "player_id", "player", "pos", "nfl",
              "adp_2qb", "board_rank_at_pick", "proj", "ts")


def _league_season(ref, season: int | None = None):
    """(league key, season) for a LeagueRef: its own season unless `season` names one."""
    return ref.key, season if season is not None else ref.season


def put_picks(rows: list[dict], ref, season: int | None = None) -> None:
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


def picks(ref, season: int | None = None) -> list[dict]:
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


def display_url(db_url: str) -> str:
    """A database URL fit to print or store: the password masked."""
    return sa.engine.make_url(db_url).render_as_string(hide_password=True)


def _read_only_engine(db_url: str) -> sa.Engine:
    """An engine on another database whose every connection is read-only (the session refuses writes). SchemaError
    on a URL that is not Postgres's, as `url()` gives."""
    eng = sa.create_engine(_check_url(db_url, "the database URL"), poolclass=sa.pool.NullPool)

    @sa.event.listens_for(eng, "connect")
    def _read_only(dbapi_conn, _rec):
        with dbapi_conn.cursor() as cur:
            cur.execute("SET default_transaction_read_only = on")
        dbapi_conn.commit()
    return eng


def picks_from_url(db_url: str, league: str) -> list[dict]:
    """The league's latest-season draft log (the `_PICK_COLS`) in another database, read-only: a practice run's own
    database, kept before it is dropped (`ffl.replay --keep NAME --from <DB URL>`)."""
    eng = _read_only_engine(db_url)
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
        return c.execute(_upsert(draft_logs, [row], ["source", "log_id"], None)
                         .returning(draft_logs.c.log_id)).first() is not None  # rowcount: -1 on psycopg


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


def add_llm_call(*, kind: str, model: str, league: str | None, player_id: int | None, input: int, output: int,
                 cost_usd: float, ok: bool) -> None:
    from datetime import datetime, timezone
    with _write() as c:
        c.execute(sa.insert(llm_calls).values(
            at=datetime.now(timezone.utc).isoformat(timespec="seconds"), kind=kind, model=model, league=league,
            player_id=player_id, input=input, output=output, cost_usd=cost_usd, ok=ok))


def get_secret(key: str) -> str | None:
    """The opened value, None with no row; a row this ring cannot open raises vault.VaultError."""
    from . import vault
    with _read() as c:
        v = c.execute(sa.select(app_secrets.c.value).where(app_secrets.c.key == key)).scalar()
    return None if v is None else vault.open_(v, aad=key)


def put_secret(key: str, plain: str) -> None:
    from . import vault
    row = {"key": key, "value": vault.seal(plain, aad=key), "updated_at": _iso(_utc_now())}
    with _write() as c:
        c.execute(_upsert(app_secrets, [row], ["key"], ["value", "updated_at"]))


def del_secret(key: str) -> None:
    with _write() as c:
        c.execute(sa.delete(app_secrets).where(app_secrets.c.key == key))


def llm_usage(days: int) -> list[dict]:
    """The last `days` UTC days of llm_calls (today included), one row per (day, kind): calls, input, output, cost_usd."""
    from datetime import datetime, timedelta, timezone
    t = llm_calls
    since = (datetime.now(timezone.utc) - timedelta(days=days - 1)).date().isoformat()
    day = sa.func.substr(t.c.at, 1, 10).label("day")
    q = (sa.select(day, t.c.kind, sa.func.count().label("calls"), sa.func.sum(t.c.input).label("input"),
                   sa.func.sum(t.c.output).label("output"), sa.func.sum(t.c.cost_usd).label("cost_usd"))
         .where(t.c.at >= since).group_by(day, t.c.kind).order_by(day, t.c.kind))
    with _read() as c:
        return [{"day": r.day, "kind": r.kind, "calls": r.calls, "input": int(r.input), "output": int(r.output),
                 "cost_usd": float(r.cost_usd)} for r in c.execute(q)]


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


def put_outlooks(week: int, entries: dict[int, dict], season: int = SEASON) -> None:
    """Upsert each player's entry; never deletes."""
    if not entries:
        return
    from datetime import datetime
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    vals = [{"season": season, "week": week, "player_id": int(pid), "entry": e,
             "updated": now} for pid, e in entries.items()]
    with _write() as c:
        c.execute(_upsert(outlooks, vals, _KEYS["outlooks"], ["entry", "updated"]))


def outlooks_for(week: int, season: int = SEASON, player_ids=None) -> dict[int, dict]:
    """The week's entries by player id; with `player_ids`, only theirs (a page reads its own players)."""
    t = outlooks
    q = sa.select(t.c.player_id, t.c.entry).where(t.c.season == season, t.c.week == week)
    if player_ids is not None:
        if not (ids := list(player_ids)):
            return {}
        q = q.where(t.c.player_id.in_(ids))
    with _read() as c:
        return {pid: e for pid, e in c.execute(q)}


def outlook_ids(week: int, player_ids, season: int = SEASON) -> set[int]:
    """Which of `player_ids` have bullets this week; reads the ids only, not the entries."""
    if not (ids := list(player_ids)):
        return set()
    t = outlooks
    q = sa.select(t.c.player_id).where(t.c.season == season, t.c.week == week, t.c.player_id.in_(ids),
                                       sa.func.json_typeof(t.c.entry["bullets"]) == "array")
    with _read() as c:
        return set(c.execute(q).scalars())


_CAL_COLS = ("player_id", "name", "pos", "model", "l3", "weight", "games_left")


def put_snapshot(week: int, k: float, rows: list[dict], season: int = SEASON, *, ref) -> bool:
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


def snapshots(season: int = SEASON, *, ref) -> list[dict]:
    """Every week's snapshot, by week, in the shape the week JSON files had."""
    cs, cr, league = calibration_snapshots, calibration_rows, _league_key(ref)
    with _read() as c:
        snaps = [{"week": w, "k": k, "rows": []} for w, k in c.execute(
            sa.select(cs.c.week, cs.c.k).where(cs.c.league == league, cs.c.season == season).order_by(cs.c.week))]
        by_week = {s["week"]: s for s in snaps}
        for r in c.execute(sa.select(cr).where(cr.c.league == league, cr.c.season == season)
                           .order_by(cr.c.week, cr.c.player_id)):
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


def put_projection_rows(week: int, kind: str, rows: list[dict], season: int = SEASON, *, ref) -> int:
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


def projection_rows(kind: str, season: int = SEASON, week: int | None = None, *, ref) -> list[dict]:
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


def put_claim_plan(plan: dict, season: int = SEASON, *, ref) -> None:
    """The week's waiver claim plan of the member's team, replaced whole (upsert by week)."""
    from datetime import datetime
    league, team = _team_of(ref)
    with _write() as c:
        c.execute(_upsert(claim_plans, [{"league": league, "season": season, "team_id": team, "week": plan["week"],
                                         "plan": plan, "updated": datetime.now().isoformat(timespec="seconds")}],
                          _KEYS["claim_plans"], ["plan", "updated"]))


def claim_plan(week: int, season: int = SEASON, *, ref) -> dict | None:
    t, (league, team) = claim_plans, _team_of(ref)
    q = sa.select(t.c.plan).where(t.c.league == league, t.c.season == season, t.c.team_id == team, t.c.week == week)
    with _read() as c:
        return c.execute(q).scalar()


def put_sit(week: int, player_id: int, sat: bool, season: int = SEASON, *, ref) -> None:
    """The member sits him for the week (a repeat is a no-op) or stands him back up (delete)."""
    from datetime import datetime
    user, league = _member(ref)
    with _write() as c:
        if sat:
            c.execute(_upsert(sits, [{"user_id": user, "league": league, "season": season, "week": week,
                                      "player_id": player_id,
                                      "at": datetime.now().astimezone().isoformat(timespec="seconds")}],
                              ["user_id", "league", "season", "week", "player_id"], None))
        else:
            c.execute(sa.delete(sits).where(sits.c.user_id == user, sits.c.league == league, sits.c.season == season,
                                            sits.c.week == week, sits.c.player_id == player_id))


def watched_ids(season: int = SEASON, *, ref) -> list[int]:
    t, (user, league) = watchlist, _member(ref)
    q = (sa.select(t.c.player_id).where(t.c.user_id == user, t.c.league == league, t.c.season == season)
         .order_by(t.c.added_at, t.c.player_id))
    with _read() as c:
        return list(c.execute(q).scalars())


def put_watch(player_id: int, watched: bool, season: int = SEASON, *, ref) -> None:
    """The member watches him (a repeat is a no-op) or stops (delete the watch; his signals stay)."""
    t, (user, league) = watchlist, _member(ref)
    with _write() as c:
        if watched:
            c.execute(_upsert(t, [{"user_id": user, "league": league, "season": season, "player_id": player_id,
                                   "added_at": _now()}], ["user_id", "league", "season", "player_id"], None))
        else:
            c.execute(sa.delete(t).where(t.c.user_id == user, t.c.league == league, t.c.season == season,
                                         t.c.player_id == player_id))


_SIGNAL_COLS = ("key", "player_id", "kind", "text", "at")


def add_watch_signals(rows: list[dict], season: int = SEASON, *, ref) -> list[dict]:
    """Store the member's signals not already known (key: user, league, season, key). Returns only the rows newly
    inserted."""
    if not rows:
        return []
    t, (user, league) = watch_signals_table, _member(ref)
    offered: dict[str, dict] = {}
    for r in rows:
        offered.setdefault(r["key"], {k: r[k] for k in _SIGNAL_COLS})
    # new = what this INSERT made (RETURNING), so a concurrent writer's rows are not reported twice; the keys go in
    # sorted, so two writers offering overlapping signals take their row locks in one order and cannot deadlock
    with _write() as c:
        made = set(c.execute(_upsert(t, [{"user_id": user, "league": league, "season": season, **offered[k]}
                                         for k in sorted(offered)],
                                     ["user_id", "league", "season", "key"], None).returning(t.c.key)).scalars())
    return [v for k, v in offered.items() if k in made]


def watch_signals(since: str, season: int = SEASON, *, ref) -> list[dict]:
    """The member's signals with `at` >= since, newest first."""
    t, (user, league) = watch_signals_table, _member(ref)
    q = (sa.select(*[t.c[k] for k in _SIGNAL_COLS])
         .where(t.c.user_id == user, t.c.league == league, t.c.season == season, t.c.at >= since)
         .order_by(t.c.at.desc(), t.c.key))
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def seat_map(season: int = SEASON, *, ref) -> dict[int, int]:
    league, team = _team_of(ref)
    q = sa.select(seats.c.player_id, seats.c.seat_id).where(seats.c.league == league, seats.c.season == season,
                                                            seats.c.team_id == team)
    with _read() as c:
        return dict(c.execute(q).all())


def put_seats(rows: dict[int, int], season: int = SEASON, *, ref) -> None:
    """Upsert player -> seat of the member's team in one transaction."""
    if not rows:
        return
    league, team = _team_of(ref)
    with _write() as c:
        c.execute(_upsert(seats, [{"league": league, "season": season, "team_id": team, "player_id": p, "seat_id": s}
                                  for p, s in rows.items()],
                          ["league", "season", "team_id", "player_id"], ["seat_id"]))


def sat_ids(week: int, season: int = SEASON, *, ref) -> set[int]:
    user, league = _member(ref)
    q = sa.select(sits.c.player_id).where(sits.c.user_id == user, sits.c.league == league, sits.c.season == season,
                                          sits.c.week == week)
    with _read() as c:
        return set(c.execute(q).scalars())


def _now() -> str:
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _push_aad(endpoint: str) -> str:
    return f"push_subscriptions:{endpoint}:keys"


def put_subscription(endpoint: str, keys: dict, ua: str = "", *, user_id: int) -> None:
    """One device's push subscription, the user's; the same endpoint again replaces its keys (browsers rotate them)
    and moves it to whoever subscribed it last (one browser, another user signed in). The keys are stored sealed."""
    from . import vault
    sealed = vault.seal_json(keys, aad=_push_aad(endpoint))
    with _write() as c:
        c.execute(_upsert(push_subscriptions, [{"endpoint": endpoint, "keys": sealed, "ua": ua, "created_at": _now(),
                                                "user_id": user_id}],
                          ["endpoint"], ["keys", "ua", "user_id"]))


def drop_subscription(endpoint: str, *, user_id: int | None) -> None:
    """Forget a device: the user's own (`user_id`), or, for the push service's 404/410 (None), whoever's it is."""
    t = push_subscriptions
    q = sa.delete(t).where(t.c.endpoint == endpoint)
    with _write() as c:
        c.execute(q if user_id is None else q.where(t.c.user_id == user_id))


def subscriptions(*, user_id: int) -> list[dict]:
    """The user's devices, their keys opened. A device whose keys this ring cannot open (sealed under another ring) is
    left out, logged: it cannot be pushed to, and the browser's next subscribe replaces it."""
    from . import vault
    t = push_subscriptions
    with _read() as c:
        rows = [dict(r._mapping) for r in c.execute(sa.select(t).where(t.c.user_id == user_id)
                                                     .order_by(t.c.created_at, t.c.endpoint))]
    out = []
    for r in rows:
        try:
            out.append({**r, "keys": vault.open_json(r["keys"], aad=_push_aad(r["endpoint"]))})
        except vault.VaultError as e:
            log.warning("push device %s left out: %s", r["endpoint"][:40], e)
    return out


_NOTICE_IN = ("key", "kind", "title", "body", "url", "created_at")


def add_notices(rows: list[dict], season: int = SEASON, *, ref) -> int:
    """Queue the member's notices not already known (key: user, league, season, key). Returns how many were new."""
    if not rows:
        return 0
    user, league = _member(ref)
    vals = [{"user_id": user, "league": league, "season": season, **{k: r[k] for k in _NOTICE_IN}} for r in rows]
    with _write() as c:
        return len(c.execute(_upsert(notices, vals, ["user_id", "league", "season", "key"], None)
                             .returning(notices.c.key)).all())


_NOTICE_COLS = ("key", "kind", "title", "body", "url", "created_at", "sent_at", "outcome")


def _notices_of(ref, season: int):
    user, league = _member(ref)
    return sa.and_(notices.c.user_id == user, notices.c.league == league, notices.c.season == season)


def unsent_notices(season: int = SEASON, *, ref) -> list[dict]:
    t = notices
    q = (sa.select(*[t.c[k] for k in _NOTICE_COLS]).where(_notices_of(ref, season), t.c.sent_at.is_(None),
                                                          t.c.read_at.is_(None), t.c.deleted_at.is_(None))
         .order_by(t.c.created_at, t.c.key))
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def mark_notices(keys: list[str], outcome: str, at: str, season: int = SEASON, *, ref) -> None:
    if not keys:
        return
    t = notices
    with _write() as c:
        c.execute(sa.update(t).where(_notices_of(ref, season), t.c.key.in_(keys)).values(sent_at=at, outcome=outcome))


def recent_notices(limit: int = 20, season: int = SEASON, *, ref) -> list[dict]:
    t = notices
    q = (sa.select(*[t.c[k] for k in _NOTICE_COLS]).where(_notices_of(ref, season))
         .order_by(t.c.created_at.desc(), t.c.key.desc()).limit(limit))
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


# A transaction's key is move:{epoch ms}-{team}-{action}-{player} (ffl/journal.py); a D/ST's player id is negative.
_MOVE_GROUP = r"^(?:move:)?(\d+-\d+)-[^-]*-[+-]?\d+$"


def inbox_notices(off: list[tuple[str, str]], leagues: list[str], limit: int, once: tuple[str, ...],
                  season: int = SEASON, *, user_id: int) -> tuple[int, list[dict]]:
    """The in-app list, grouped here so a count reads no rows: (unread items, the rows of the newest `limit` items,
    newest item first). An item is one notice in its league, a claim and its drop (one team at one instant), or, for
    a key starting with `once`, one key across the leagues. One row of an item read or deleted reads or deletes the
    item; one with its (league, kind) in `off` is hidden, and an item with only those is not listed."""
    if not leagues:
        return 0, []
    t = notices
    alone = sa.or_(*[t.c.key.startswith(p, autoescape=True) for p in once])
    grp = sa.case((alone, t.c.key),
                  else_=t.c.league + "|" + sa.func.coalesce(sa.func.substring(t.c.key, _MOVE_GROUP), t.c.key)).label("grp")
    shown = (sa.not_(sa.tuple_(t.c.league, t.c.kind).in_(off)) if off else sa.true()).label("shown")
    mine = (t.c.user_id == user_id, t.c.season == season, t.c.league.in_(leagues))
    items = (sa.select(grp, sa.func.bool_or(t.c.read_at.is_not(None)).label("is_read"),
                       sa.func.max(t.c.created_at).filter(shown).label("newest"))
             .where(*mine).group_by(grp)
             .having(sa.and_(sa.func.bool_or(shown), sa.not_(sa.func.bool_or(t.c.deleted_at.is_not(None))))).subquery())
    with _read() as c:
        unread = c.execute(sa.select(sa.func.count()).select_from(items).where(sa.not_(items.c.is_read))).scalar_one()
        if limit <= 0:
            return unread, []
        page = sa.select(items).order_by(items.c.newest.desc(), items.c.grp.desc()).limit(limit).subquery()
        q = (sa.select(grp, shown, page.c.is_read, t.c.league, *[t.c[k] for k in _NOTICE_COLS])
             .join_from(t, page, grp == page.c.grp).where(*mine)
             .order_by(page.c.newest.desc(), page.c.grp.desc(), t.c.created_at.desc(), t.c.key.desc()))
        return unread, [dict(r._mapping) for r in c.execute(q)]


def stamp_notices(rows: list[tuple[str, str]], column: str, at: str, season: int = SEASON, *, user_id: int) -> None:
    """Set `read_at` or `deleted_at` on the user's (league, key) rows that have none."""
    if not rows:
        return
    t = notices
    with _write() as c:
        c.execute(sa.update(t).where(t.c.user_id == user_id, t.c.season == season, t.c[column].is_(None),
                                     sa.tuple_(t.c.league, t.c.key).in_(rows)).values({column: at}))


def read_all_notices(at: str, season: int = SEASON, *, user_id: int) -> None:
    t = notices
    with _write() as c:
        c.execute(sa.update(t).where(t.c.user_id == user_id, t.c.season == season, t.c.read_at.is_(None))
                  .values(read_at=at))


def read_pushed_notices(kinds: list[str] | None, key: str | None, at: str, season: int = SEASON, *, ref) -> None:
    """Read what one push stood for in a league: its notice (`key`), or every notice of `kinds` already pushed."""
    t = notices
    which = t.c.key == key if key else sa.and_(t.c.kind.in_(kinds), t.c.outcome == "sent")
    with _write() as c:
        c.execute(sa.update(t).where(_notices_of(ref, season), t.c.read_at.is_(None), which).values(read_at=at))


_STATE_HOMES = {"user": USER_STATE_KEYS, "member": MEMBER_STATE_KEYS, "team": TEAM_STATE_KEYS,
                "league": LEAGUE_STATE_KEYS}


def _check_key(key: str, home: str) -> None:
    """A state key is read and written where it lives: global (notify_state), a user's, a league's (a member's and a
    team's keys go through the league functions, which take the member's ref)."""
    where = next((h for h, keys in _STATE_HOMES.items() if key in keys), "global")
    if where in ("member", "team"):
        where = "league"
    if where != home:
        fix = {"global": "put/get_state", "user": "put/get_state(user_id=)", "league": "put/get_league_state"}[where]
        raise ValueError(f"state key {key!r} is {where}: use {fix}")


def _state_row(key: str, season: int, user_id: int | None) -> tuple[sa.Table, dict]:
    """The table and key columns of a global state key (user_id None) or a user's (USER_STATE_KEYS: user_id required)."""
    if key in USER_STATE_KEYS:
        if user_id is None:
            raise ValueError(f"state key {key!r} is a user's: pass user_id")
        _check_key(key, "user")
        return user_state, {"user_id": user_id, "season": season, "key": key}
    _check_key(key, "global")
    if user_id is not None:
        raise ValueError(f"state key {key!r} is global, not a user's")
    return notify_state, {"season": season, "key": key}


def get_state(key: str, default=None, season: int = SEASON, *, user_id: int | None = None):
    """A global state key's value; a user's (USER_STATE_KEYS) with `user_id`."""
    t, keys = _state_row(key, season, user_id)
    q = sa.select(t.c.value).where(*[t.c[k] == v for k, v in keys.items()])
    with _read() as c:
        v = c.execute(q).scalar()
    return default if v is None else v


def put_state(key: str, value, season: int = SEASON, *, user_id: int | None = None) -> None:
    t, keys = _state_row(key, season, user_id)
    with _write() as c:
        c.execute(_upsert(t, [{**keys, "value": value}], list(keys), ["value"]))


def _update(table: sa.Table, keys: dict, fn, default):
    """Read-modify-write of one state row in one transaction: insert it if absent, lock it (SELECT ... FOR UPDATE: a
    concurrent updater waits, then reads this one's committed value), write fn(value), return it. `fn` is pure and
    quick (no ESPN call, no other db call: it runs holding the row lock); an exception in it writes nothing."""
    where = [table.c[k] == v for k, v in keys.items()]
    with _write() as c:
        c.execute(_upsert(table, [{**keys, "value": default}], list(keys), None))
        v = c.execute(sa.select(table.c.value).where(*where).with_for_update()).scalar()
        new = fn(default if v is None else v)
        c.execute(sa.update(table).where(*where).values(value=new))
    return new


def update_state(key: str, fn, default=None, season: int = SEASON, *, user_id: int | None = None):
    """A global state key's value (a user's with `user_id`) changed by `fn` (its value, or `default` when unset)
    without losing a concurrent update (a get then a put can). Returns the value written."""
    return _update(*_state_row(key, season, user_id), fn, default)


def _league_state_row(key: str, season: int, ref) -> tuple[sa.Table, dict]:
    """Where a league-side key lives for the member `ref` is the view of: the league's own, the member's
    (MEMBER_STATE_KEYS) or the member's team's (TEAM_STATE_KEYS)."""
    _check_key(key, "league")
    if key in MEMBER_STATE_KEYS:
        user, league = _member(ref)
        return member_state, {"user_id": user, "league": league, "season": season, "key": key}
    if key in TEAM_STATE_KEYS:
        league, team = _team_of(ref)
        return team_state, {"league": league, "season": season, "team_id": team, "key": key}
    return league_state, {"league": _league_key(ref), "season": season, "key": key}


def update_league_state(key: str, fn, default=None, season: int = SEASON, *, ref):
    """A league-side state key changed by `fn`, as update_state."""
    return _update(*_league_state_row(key, season, ref), fn, default)


def get_league_state(key: str, default=None, season: int = SEASON, *, ref):
    """A league-side state key: the league's, the member's or their team's, by the key (_league_state_row)."""
    t, keys = _league_state_row(key, season, ref)
    q = sa.select(t.c.value).where(*[t.c[k] == v for k, v in keys.items()])
    with _read() as c:
        v = c.execute(q).scalar()
    return default if v is None else v


def put_league_state(key: str, value, season: int = SEASON, *, ref) -> None:
    t, keys = _league_state_row(key, season, ref)
    with _write() as c:
        c.execute(_upsert(t, [{**keys, "value": value}], list(keys), ["value"]))


def _page_owner(ref) -> tuple[str, int]:
    """(league key, user id) whose page rows a LeagueRef names: the member's own (ffl-soc.6.2.2); 0 when the ref is no
    member's."""
    return ref.key, ref.user_id or 0


def _pages_of(ref, season: int):
    league, user = _page_owner(ref)
    return sa.and_(page_cache.c.user_id == user, page_cache.c.league == league, page_cache.c.season == season)


def put_page(key: str, payload, built_at: float, season: int = SEASON, *, ref) -> None:
    league, user = _page_owner(ref)
    with _write() as c:
        c.execute(_upsert(page_cache, [{"user_id": user, "league": league, "season": season, "key": key,
                                        "payload": payload, "built_at": built_at}],
                          ["user_id", "league", "season", "key"], ["payload", "built_at"]))


def drop_pages(keys: list[str], season: int = SEASON, *, ref) -> None:
    if not keys:
        return
    where = _pages_of(ref, season)
    with _write() as c:
        c.execute(page_cache.delete().where(where, page_cache.c.key.in_(keys)))


def drop_page_prefixes(prefixes: tuple[str, ...], season: int = SEASON, *, ref) -> None:
    """Delete every row keyed `prefix` or `prefix:...`."""
    k = page_cache.c.key
    match = sa.or_(*[sa.or_(k == p, k.startswith(p + ":", autoescape=True)) for p in prefixes])
    where = _pages_of(ref, season)
    with _write() as c:
        c.execute(page_cache.delete().where(where, match))


def clear_pages(season: int = SEASON, *, ref) -> None:
    where = _pages_of(ref, season)
    with _write() as c:
        c.execute(page_cache.delete().where(where))


def pages(season: int = SEASON, *, ref) -> list[dict]:
    """Every stored page: {key, payload, built_at}."""
    t = page_cache
    q = sa.select(t.c.key, t.c.payload, t.c.built_at).where(_pages_of(ref, season))
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


def notice_keys(prefix: str, season: int = SEASON, *, ref) -> list[str]:
    """Keys of the notices already pushed (sent_at set, outcome sent) that start with `prefix`, in any of the member's
    leagues: a news item reaches a user once, whichever league queued it (#480)."""
    t = notices
    q = (sa.select(t.c.key).distinct().where(t.c.user_id == _member(ref)[0], t.c.season == season,
                                             t.c.sent_at.is_not(None),
                                  t.c.outcome == "sent", t.c.key.startswith(prefix, autoescape=True)).order_by(t.c.key))
    with _read() as c:
        return list(c.execute(q).scalars())


def notices_since(prefix: str, since: str, season: int = SEASON, *, ref) -> list[dict]:
    """{key, created_at, outcome} of the notices starting with `prefix` queued at or after `since`, in any of the
    member's leagues, any outcome (None = still waiting). Compared as text, so the caller passes a margin and re-checks
    the times."""
    t = notices
    q = (sa.select(t.c.key, t.c.created_at, t.c.outcome)
         .where(t.c.user_id == _member(ref)[0], t.c.season == season, t.c.created_at >= since,
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
        old = c.execute(sa.select(t.c.hash, t.c.title, t.c.text).where(t.c.season == season, t.c.id == item_id)
                        .with_for_update()).first()
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
        # the story locked before the count: two of its items withdrawn at once must not each count the other live
        c.execute(sa.select(s.c.id).where(s.c.season == season, s.c.id == sid).with_for_update())
        live = c.execute(sa.select(sa.func.count()).select_from(t)
                         .where(t.c.season == season, t.c.story_id == sid, t.c.withdrawn_at.is_(None))).scalar()
        cur = c.execute(sa.select(s.c.status).where(s.c.season == season, s.c.id == sid)).scalar()
        if live or cur in (None, "retracted"):
            _relatest(c, sid, season)
            return None
        _bump_story(c, sid, season, status="retracted", status_at=at, updated_at=at, latest_at=at)
        return dict(c.execute(sa.select(*[k for k in s.c if k.name != "embedding"]).where(s.c.id == sid)).first()._mapping)


def set_story_status(story_id: int, status: str, at: str, note: str | None, season: int = SEASON) -> None:
    """A correction or retraction: the story re-surfaces (rev+1, updated_at) so it is written and pushed again."""
    with _write() as c:
        _bump_story(c, story_id, season, status=status, status_at=at, status_note=note, updated_at=at)


def untagged_items(season: int = SEASON) -> list[dict]:
    """Newest first: in a backlog (a first run, the tagger down a while) today's news is tagged before last week's."""
    t = news_items_table
    q = (sa.select(t).where(t.c.season == season, t.c.tagged_at.is_(None), t.c.withdrawn_at.is_(None))
         .order_by(t.c.published_at.desc(), t.c.id.desc()))
    with _read() as c:
        return [_item_out(r) for r in c.execute(q)]


def tag_item(item_id: int, player_ids: list[int], answers: dict | None, story_id: int | None, at: str,
             season: int = SEASON) -> None:
    t = news_items_table
    with _write() as c:
        c.execute(sa.update(t).where(t.c.season == season, t.c.id == item_id)
                  .values(player_ids=player_ids, jev=answers, story_id=story_id, tagged_at=at))
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
    out = [{k: v for k, v in r._mapping.items() if k not in ("season", "embedding")} for r in rows]
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
    """The tagger found the written headline stale: the story is written again, at the same rev (so it does not push)."""
    t = news_stories
    with _write() as c:
        c.execute(sa.update(t).where(t.c.season == season, t.c.id == story_id).values(reword=t.c.reword + 1))


def set_story_flag(story_id: int, player_id: int, flag: bool, season: int = SEASON) -> None:
    """Whether the check doubts the row's arrow (`newsdesk.check`): a flagged row never pushes."""
    p = news_story_players
    with _write() as c:
        c.execute(sa.update(p).where(p.c.season == season, p.c.story_id == story_id, p.c.player_id == player_id).values(flag=flag))


def unwritten_stories(limit: int | None = None, season: int = SEASON) -> list[dict]:
    """Stories whose rev moved past their words, or whose headline the tagger found stale; the newest news first, so the
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
                         .values(headline=headline, overview=overview, written_rev=rev, reword=0, embedding=None))
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


def unembedded_stories(season: int = SEASON) -> list[dict]:
    """Written stories with no vector of their words (new, rewritten, or from before embeddings), newest news first:
    only the words embedded ({id, headline, overview, reasons}), never the sources, so a whole season's backfill is light."""
    t, p = news_stories, news_story_players
    q = (sa.select(t.c.id, t.c.headline, t.c.overview)
         .where(t.c.season == season, t.c.written_rev >= 1, t.c.embedding.is_(None))
         .order_by(t.c.latest_at.desc(), t.c.id.desc()))
    with _read() as c:
        out = [{**r._mapping, "reasons": []} for r in c.execute(q)]
        by_id = {s["id"]: s for s in out}
        if by_id:
            for r in c.execute(sa.select(p.c.story_id, p.c.reason)
                               .where(p.c.season == season, p.c.story_id.in_(by_id), p.c.reason.is_not(None))
                               .order_by(p.c.story_id, p.c.player_id)):
                by_id[r.story_id]["reasons"].append(r.reason)
        return out


def put_story_embedding(story_id: int, headline: str, vec: list[float], season: int = SEASON) -> bool:
    """Store the vector, unless the story was rewritten since it was read (put_story_words cleared or changed it)."""
    t = news_stories
    with _write() as c:
        return bool(c.execute(sa.update(t).where(t.c.season == season, t.c.id == story_id, t.c.embedding.is_(None),
                                                 t.c.headline == headline).values(embedding=vec)).rowcount)


def unchunked_items(limit: int, season: int = SEASON) -> list[dict]:
    """Live items on a story whose latest text has no chunks yet (new, revised, or from before chunking), newest first:
    {id, hash, title, text}."""
    t, k = news_items_table, news_item_chunks
    done = sa.exists().where(k.c.season == t.c.season, k.c.item_id == t.c.id, k.c.hash == t.c.hash)
    q = (sa.select(t.c.id, t.c.hash, t.c.title, t.c.text)
         .where(t.c.season == season, t.c.story_id.is_not(None), t.c.withdrawn_at.is_(None), ~done)
         .order_by(t.c.published_at.desc(), t.c.id.desc()).limit(limit))
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def put_item_chunks(item_id: int, hash: str, pieces: list[tuple[str, list[float]]], season: int = SEASON) -> bool:
    """Replace the item's chunks with `pieces` (text, vector), unless its text was revised since it was read."""
    t, k = news_items_table, news_item_chunks
    with _write() as c:
        cur = c.execute(sa.select(t.c.hash).where(t.c.season == season, t.c.id == item_id).with_for_update()).scalar()
        if cur != hash:
            return False
        c.execute(sa.delete(k).where(k.c.season == season, k.c.item_id == item_id))
        if pieces:
            c.execute(sa.insert(k), [{"season": season, "item_id": item_id, "pos": n, "hash": hash, "text": text,
                                      "embedding": vec} for n, (text, vec) in enumerate(pieces)])
        return True


def _nearest_stories(c, qvec: list[float], since: str, limit: int, season: int) -> list:
    """(story id, distance, item id, passage) of the `limit` written stories nearest `qvec`, each by its best vector:
    its words (item id and passage None) or a chunk of one of its live items' latest text."""
    s, t, k = news_stories, news_items_table, news_item_chunks
    written = (s.c.season == season, s.c.written_rev >= 1, s.c.latest_at >= since)
    words = (sa.select(s.c.id.label("sid"), s.c.embedding.cosine_distance(qvec).label("d"),
                       sa.cast(sa.null(), sa.Integer).label("item_id"), sa.cast(sa.null(), sa.String).label("text"))
             .where(*written, s.c.embedding.is_not(None)))
    pieces = (sa.select(s.c.id, k.c.embedding.cosine_distance(qvec), k.c.item_id, k.c.text)
              .select_from(k.join(t, sa.and_(t.c.season == k.c.season, t.c.id == k.c.item_id, t.c.hash == k.c.hash))
                           .join(s, sa.and_(s.c.season == t.c.season, s.c.id == t.c.story_id)))
              .where(*written, t.c.withdrawn_at.is_(None)))
    u = sa.union_all(words, pieces).subquery()
    best = sa.select(u).ext(distinct_on(u.c.sid)).order_by(u.c.sid, u.c.d).subquery()
    return c.execute(sa.select(best).order_by(best.c.d, best.c.sid).limit(limit)).all()


def search_stories(qvec: list[float] | None, q: str, since: str, limit: int = 10, season: int = SEASON) -> list[dict]:
    """Written stories with news at or after `since`: those whose headline contains `q` first (an exact name still
    wins), newest first, then the `limit` nearest `qvec` by cosine distance over their words and their sources' text
    (none when `qvec` is None). Each carries `match`: the source passage it was found by ({item_id, text}), else None."""
    t = news_stories
    base = sa.select(t).where(t.c.season == season, t.c.written_rev >= 1, t.c.latest_at >= since)
    with _read() as c:
        rows = c.execute(base.where(t.c.headline.icontains(q, autoescape=True))
                         .order_by(t.c.latest_at.desc(), t.c.id.desc()).limit(limit)).all() if q.strip() else []
        near = _nearest_stories(c, qvec, since, limit, season) if qvec is not None else []
        match = {n.sid: {"item_id": n.item_id, "text": n.text} if n.item_id is not None else None for n in near}
        ids = [n.sid for n in near if n.sid not in {r.id for r in rows}]
        by_id = {r.id: r for r in c.execute(base.where(t.c.id.in_(ids))).all()} if ids else {}
        out = _stories_out(c, rows + [by_id[i] for i in ids if i in by_id], season)
        for s in out:
            s["match"] = match.get(s["id"])
        return out


def _decision_out(r) -> dict:
    # user, league and season are implied by the read and import_key is the import's own dedupe key: none is part of a
    # decision
    return {k: v for k, v in r._mapping.items() if k not in ("user_id", "league", "season", "import_key")}


def _decisions_of(ref, season: int):
    t, (user, league) = decisions_table, _member(ref)
    return sa.and_(t.c.user_id == user, t.c.league == league, t.c.season == season)


def add_decision(row: dict, season: int = SEASON, *, ref) -> int:
    """The member's decision; its id."""
    user, league = _member(ref)
    with _write() as c:
        return c.execute(sa.insert(decisions_table).values({**row, "user_id": user, "league": league, "season": season})
                         .returning(decisions_table.c.id)).scalar()


def put_week_claims(week: int, row: dict, season: int = SEASON, *, ref) -> dict:
    """The app claims row of the week's plan generation (facts.gen, the plans the waiver run retired before it): a
    resubmit replaces what was sent, keeping the rationale and outcome given since. A plan started after Wednesday's
    run is the same ESPN week but a new decision, so it gets its own row and never inherits the run's reason."""
    t = decisions_table
    keep = {"rationale", "rationale_note", "outcome", "outcome_at"}
    gen = (row.get("facts") or {}).get("gen", 0)
    user, league = _member(ref)
    with _write() as c:
        # two submits of one week must not both find no row and both insert (held to the transaction's end)
        c.execute(sa.select(sa.func.pg_advisory_xact_lock(
            sa.func.hashtext(f"claims:{user}:{league}:{season}:{week}"))))
        rows = c.execute(sa.select(t.c.id, t.c.rationale, t.c.facts).where(
            _decisions_of(ref, season), t.c.week == week, t.c.kind == "claims", t.c.source == "app")).all()
        old = next((r for r in rows if (r.facts or {}).get("gen", 0) == gen), None)
        if old is None:
            i = c.execute(sa.insert(t).values({**row, "user_id": user, "league": league, "season": season,
                                               "week": week}).returning(t.c.id)).scalar()
            return {"id": i, "needs_rationale": row.get("rationale") is None}
        c.execute(sa.update(t).where(t.c.id == old.id).values({k: v for k, v in row.items() if k not in keep}))
        return {"id": old.id, "needs_rationale": old.rationale is None}


def update_decision(id: int, fields: dict, season: int = SEASON, *, ref) -> dict | None:
    t = decisions_table
    with _write() as c:
        if not c.execute(sa.update(t).where(_decisions_of(ref, season), t.c.id == id).values(fields)).rowcount:
            return None
        return _decision_out(c.execute(sa.select(t).where(t.c.id == id)).one())


def decision(id: int, season: int = SEASON, *, ref) -> dict | None:
    t = decisions_table
    q = sa.select(t).where(_decisions_of(ref, season), t.c.id == id)
    with _read() as c:
        r = c.execute(q).first()
    return _decision_out(r) if r else None


def decisions(season: int = SEASON, *, ref) -> list[dict]:
    """The member's decisions in the league, newest first."""
    t = decisions_table
    q = sa.select(t).where(_decisions_of(ref, season)).order_by(t.c.at.desc(), t.c.id.desc())
    with _read() as c:
        return [_decision_out(r) for r in c.execute(q)]


# the natural key of each table the file import writes (ON CONFLICT DO NOTHING on it); sigma_fits stays global
_KEYS = {"transactions": ["league", "season", "id"], "draft_picks": ["league", "season", "overall"],
         "results": ["league", "season", "week"], "plays": ["league", "season", "week", "player_id", "at", "total"],
         "outlooks": ["season", "week", "player_id"], "sigma_fits": ["season", "fitted_at"],
         "claim_plans": ["league", "season", "team_id", "week"], "decisions": ["league", "season", "import_key"]}


def insert_new(table_name: str, rows: list[dict]) -> int:
    """Insert the rows not already present (DO NOTHING on the table's key); returns how many were new.
    Rows carry their own season and league. For the file import, which must never overwrite."""
    if not rows:
        return 0
    t = metadata.tables[table_name]
    pk = list(t.primary_key.columns)[0]
    with _write() as c:  # batches: one multi-row VALUES would pass the bound-parameter limit on a big table
        return sum(len(c.execute(_upsert(t, rows[i:i + 500], _KEYS[table_name], None).returning(pk)).all())
                   for i in range(0, len(rows), 500))


def count(table_name: str, season: int = SEASON, *, ref) -> int:
    """The season's rows in a table; in one league (`ref`, None the home one) when the table has the column."""
    t = metadata.tables[table_name]
    q = sa.select(sa.func.count()).select_from(t).where(t.c.season == season)
    if "league" in t.c:
        q = q.where(t.c.league == _league_key(ref))
    with _read() as c:
        return c.execute(q).scalar()


# ---- accounts ----

SESSION_DAYS = 90
_SLIDE_AFTER_S = 3600


def _utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _iso(dt) -> str:
    return dt.isoformat(timespec="seconds")  # UTC with +00:00, so strings compare as times


def _token_hash(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()


def create_user(email: str | None, name: str | None) -> int:
    with _write() as c:
        return c.execute(sa.insert(users).values(email=email, name=name, created_at=_iso(_utc_now()))
                         .returning(users.c.id)).scalar_one()


@contextmanager
def _taken():
    """A unique key (username or email, any case) already held is `Taken`."""
    try:
        yield
    except IntegrityError as e:
        raise Taken(str(e.orig)) from e


def create_invite(email: str | None, name: str | None) -> int:
    """A user with no username or password yet: they set both through a setup link."""
    with _taken():
        return create_user(email, name)


def user_by_username(username: str) -> dict | None:
    with _read() as c:
        r = c.execute(sa.select(users).where(sa.func.lower(users.c.username) == username.lower())).first()
    return dict(r._mapping) if r else None


def set_password(user_id: int, password_hash: str) -> None:
    with _write() as c:
        c.execute(sa.update(users).where(users.c.id == user_id).values(password_hash=password_hash))


def set_username(user_id: int, username: str) -> None:
    with _taken(), _write() as c:
        c.execute(sa.update(users).where(users.c.id == user_id).values(username=username))


def needs_setup() -> bool:
    """No user can sign in yet: nobody has a password."""
    with _read() as c:
        return not c.execute(sa.select(sa.exists().where(users.c.password_hash.isnot(None)))).scalar_one()


def claim_first_user(username: str, password_hash: str) -> int | None:
    """The first sign-up, in one write: the oldest user (made when there is none) gets the username, password and
    admin flag, only while nobody has a password; None once someone has. The advisory lock serialises racing claims."""
    with _taken(), _write() as c:
        c.execute(sa.text("SELECT pg_advisory_xact_lock(7351)"))
        uid = c.execute(sa.text(
            "UPDATE users SET username = :u, password_hash = :h, is_admin = true "
            "WHERE id = (SELECT min(id) FROM users) "
            "AND NOT EXISTS (SELECT 1 FROM users WHERE password_hash IS NOT NULL) RETURNING id"),
            {"u": username, "h": password_hash}).scalar()
        if uid is None and not c.execute(sa.select(sa.func.count()).select_from(users)).scalar_one():
            uid = c.execute(sa.insert(users).values(username=username, password_hash=password_hash, is_admin=True,
                                                    created_at=_iso(_utc_now())).returning(users.c.id)).scalar_one()
        return uid


SETUP_DAYS = 7


def issue_setup_token(user_id: int) -> str:
    """A setup link's raw token, live for 7 days; only its hash is stored, and a new one replaces the old."""
    import secrets
    from datetime import timedelta
    token = secrets.token_urlsafe(32)
    with _write() as c:
        c.execute(sa.update(users).where(users.c.id == user_id).values(
            setup_token_hash=_token_hash(token), setup_expires=_iso(_utc_now() + timedelta(days=SETUP_DAYS))))
    return token


def user_by_setup_token(raw: str) -> dict | None:
    """The user a live setup token belongs to; None when unknown or expired."""
    with _read() as c:
        r = c.execute(sa.select(users).where(users.c.setup_token_hash == _token_hash(raw),
                                             users.c.setup_expires > _iso(_utc_now()))).first()
    return dict(r._mapping) if r else None


def clear_setup_token(user_id: int) -> None:
    with _write() as c:
        c.execute(sa.update(users).where(users.c.id == user_id).values(setup_token_hash=None, setup_expires=None))


def set_admin(user_id: int, flag: bool) -> None:
    with _write() as c:
        c.execute(sa.update(users).where(users.c.id == user_id).values(is_admin=flag))


def admin_count() -> int:
    with _read() as c:
        return c.execute(sa.select(sa.func.count()).select_from(users).where(users.c.is_admin)).scalar_one()


def list_users() -> list[dict]:
    with _read() as c:
        rows = c.execute(sa.select(users.c.id, users.c.username, users.c.email, users.c.name, users.c.is_admin,
                                   users.c.password_hash.isnot(None).label("has_password"), users.c.created_at)
                         .order_by(users.c.id)).all()
    return [dict(r._mapping) for r in rows]


def user_by_email(email: str) -> dict | None:
    with _read() as c:
        r = c.execute(sa.select(users).where(sa.func.lower(users.c.email) == email.lower()).order_by(users.c.id)).first()
    return dict(r._mapping) if r else None


def user_row(user_id: int) -> dict | None:
    with _read() as c:
        r = c.execute(sa.select(users).where(users.c.id == user_id)).first()
    return dict(r._mapping) if r else None


def create_session(user_id: int, ua: str) -> str:
    """A new session; returns the raw token (the cookie's value). Only its hash is stored."""
    import secrets
    from datetime import timedelta
    token = secrets.token_urlsafe(32)
    now = _utc_now()
    with _write() as c:
        c.execute(sa.insert(sessions).values(
            token_hash=_token_hash(token), user_id=user_id, created_at=_iso(now), last_seen=_iso(now),
            expires_at=_iso(now + timedelta(days=SESSION_DAYS)), ua=ua))
    return token


def session_user(token: str) -> dict | None:
    """The user row for a live session; None when the token is unknown or past expires_at. A session last seen
    over an hour ago slides: expires_at moves to now + 90 days and last_seen to now."""
    return session_lookup(token)[0]


def session_lookup(token: str) -> tuple[dict | None, bool]:
    """`session_user`, and whether this read slid the session (so the caller re-sends the cookie's Max-Age). Runs on
    every request: a read, with a write transaction only for the slide (once an hour per session)."""
    from datetime import datetime, timedelta
    h = _token_hash(token)
    now = _utc_now()
    with _read() as c:
        r = c.execute(sa.select(sessions.c.expires_at, sessions.c.last_seen, users)
                      .join(users, users.c.id == sessions.c.user_id).where(sessions.c.token_hash == h)).first()
    if r is None:
        return None, False
    r = dict(r._mapping)
    expires_at, last_seen = r.pop("expires_at"), r.pop("last_seen")
    if datetime.fromisoformat(expires_at) <= now:
        return None, False
    if (now - datetime.fromisoformat(last_seen)).total_seconds() <= _SLIDE_AFTER_S:
        return r, False
    with _write() as c:
        c.execute(sa.update(sessions).where(sessions.c.token_hash == h).values(
            last_seen=_iso(now), expires_at=_iso(now + timedelta(days=SESSION_DAYS))))
    return r, True


def sweep_sessions() -> int:
    """Delete every session past expires_at (ISO UTC strings compare as times); the count deleted."""
    with _write() as c:
        return c.execute(sa.delete(sessions).where(sessions.c.expires_at <= _iso(_utc_now()))).rowcount


def delete_session(token: str) -> None:
    with _write() as c:
        c.execute(sa.delete(sessions).where(sessions.c.token_hash == _token_hash(token)))


def user_sessions(user_id: int, current: str | None = None) -> list[dict]:
    """The user's live sessions, newest seen first: `id` is the stored hash (an opaque handle: the cookie cannot be
    had from it), `current` the one `current` (a raw token) is."""
    mine = _token_hash(current) if current else None
    with _read() as c:
        rows = c.execute(sa.select(sessions.c.token_hash, sessions.c.created_at, sessions.c.last_seen, sessions.c.ua)
                         .where(sessions.c.user_id == user_id, sessions.c.expires_at > _iso(_utc_now()))
                         .order_by(sessions.c.last_seen.desc())).all()
    return [{"id": r.token_hash, "created_at": r.created_at, "last_seen": r.last_seen, "ua": r.ua or "",
             "current": r.token_hash == mine} for r in rows]


def revoke_session(user_id: int, session_id: str) -> bool:
    """End one of the user's sessions by its `user_sessions` id; False when they have none by it."""
    with _write() as c:
        return c.execute(sa.delete(sessions).where(sessions.c.user_id == user_id,
                                                   sessions.c.token_hash == session_id)).rowcount > 0


def revoke_sessions(user_id: int, keep: str | None = None) -> int:
    """End every session of the user's but `keep` (a raw token; None ends them all); the count ended."""
    q = sa.delete(sessions).where(sessions.c.user_id == user_id)
    if keep:
        q = q.where(sessions.c.token_hash != _token_hash(keep))
    with _write() as c:
        return c.execute(q).rowcount


def _cred_aad(user_id: int) -> str:
    return f"espn_credentials:{user_id}:secret"


def swid_hash(swid: str) -> str:
    """An ESPN account's name here: sha256 of its SWID's GUID (braces, percent-encoding and case dropped), so one login
    however it was copied is one account. The SWID itself is only ever stored sealed."""
    import hashlib
    import urllib.parse
    guid = urllib.parse.unquote(swid).strip().strip("{}").upper()
    return hashlib.sha256(guid.encode()).hexdigest()


_UNOPENABLE: set[int] = set()   # credential rows this process's ring failed to open: not tried on every read again


def _fill_hashes(c, user_id: int) -> None:
    """Name the user's rows from before 0033 (swid_hash NULL) by opening them; one this ring cannot open stays NULL."""
    from . import vault
    t = espn_credentials
    for cid, secret in c.execute(sa.select(t.c.id, t.c.secret)
                                 .where(t.c.user_id == user_id, t.c.swid_hash.is_(None))).all():
        try:
            swid = vault.open_json(secret, aad=_cred_aad(user_id))["swid"]
        except vault.VaultError:
            _UNOPENABLE.add(cid)
            continue
        c.execute(sa.update(t).where(t.c.id == cid).values(swid_hash=swid_hash(swid)))


def put_credentials(user_id: int, espn_s2: str, swid: str) -> str:
    """The user's cookies for one ESPN account, sealed: a new SWID is another account's row, the same one's replaced;
    new cookies clear a failing mark. A row this ring cannot open (sealed under another ring: a test server's copy of
    production) is dropped, as one row per user always was replaced: left, it would stand as a dead first account
    for every membership that names none. The account (its swid_hash)."""
    from . import vault
    account = swid_hash(swid)
    sealed = vault.seal_json({"espn_s2": espn_s2, "swid": swid}, aad=_cred_aad(user_id))
    t = espn_credentials
    with _write() as c:
        _fill_hashes(c, user_id)
        c.execute(sa.delete(t).where(t.c.user_id == user_id, t.c.swid_hash.is_(None)))
        c.execute(_upsert(espn_credentials, [{"user_id": user_id, "swid_hash": account, "secret": sealed,
                                              "updated_at": _now(), "last_ok": None, "failing_since": None,
                                              "failing_reason": None}],
                          ["user_id", "swid_hash"], ["secret", "updated_at", "last_ok", "failing_since",
                                                     "failing_reason"]))
    return account


def drop_credentials(user_id: int, account: str | None = None) -> None:
    """One of the user's accounts (a swid_hash), or every one."""
    t = espn_credentials
    with _write() as c:
        _fill_hashes(c, user_id)
        q = sa.delete(t).where(t.c.user_id == user_id)
        c.execute(q if account is None else q.where(t.c.swid_hash == account))


def credential_rows(user_id: int) -> list[dict]:
    """The user's accounts, first connected first, each with its cookies opened (`espn_s2`, `swid`). A row this ring
    cannot open comes back with `unreadable` set and no cookies."""
    t = espn_credentials
    q = sa.select(t).where(t.c.user_id == user_id).order_by(t.c.id)
    with _read() as c:
        rows = [dict(r._mapping) for r in c.execute(q)]
    if any(r["swid_hash"] is None and r["id"] not in _UNOPENABLE for r in rows):
        with _write() as c:
            _fill_hashes(c, user_id)
            rows = [dict(r._mapping) for r in c.execute(q)]
    return [_opened(r) for r in rows]


def _opened(row: dict) -> dict:
    from . import vault
    row = dict(row)
    secret = row.pop("secret")
    try:
        return {**row, **vault.open_json(secret, aad=_cred_aad(row["user_id"])), "unreadable": None}
    except vault.VaultError as e:
        return {**row, "espn_s2": None, "swid": None, "unreadable": str(e)}


def credential_statuses() -> list[dict]:
    """Every account of every user, no secret: user_id, email, id, updated_at, last_ok, failing_since,
    failing_reason."""
    t = espn_credentials
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(
            sa.select(users.c.id.label("user_id"), users.c.email, t.c.id, t.c.updated_at, t.c.last_ok,
                      t.c.failing_since, t.c.failing_reason)
            .join(t, t.c.user_id == users.c.id).order_by(users.c.id, t.c.id))]


def mark_credentials(cred_id: int, *, ok: bool, reason: str | None = None) -> None:
    """A read one account's cookies passed (last_ok, clearing a failing mark) or one ESPN refused (failing_since, kept
    from the first refusal)."""
    t = espn_credentials
    now = _now()
    values = ({"last_ok": now, "failing_since": None, "failing_reason": None} if ok else
              {"failing_since": sa.func.coalesce(t.c.failing_since, now), "failing_reason": reason})
    with _write() as c:
        c.execute(sa.update(t).where(t.c.id == cred_id).values(**values))


def reseal_all() -> int:
    """Every sealed value under a key other than the ring's first, sealed again with the first (`ffl.vault rotate`);
    the count. A value the ring cannot open stops it with VaultError, nothing written."""
    from . import vault
    n = 0
    with _write() as c:
        ec = espn_credentials
        for cid, uid, secret in c.execute(sa.select(ec.c.id, ec.c.user_id, ec.c.secret)).all():
            if vault.stale(secret):
                plain = vault.open_(secret, aad=_cred_aad(uid))
                c.execute(sa.update(ec).where(ec.c.id == cid)
                          .values(secret=vault.seal(plain, aad=_cred_aad(uid))))
                n += 1
        t = push_subscriptions
        for endpoint, keys in c.execute(sa.select(t.c.endpoint, t.c["keys"])).all():
            if vault.stale(keys):
                plain = vault.open_(keys, aad=_push_aad(endpoint))
                c.execute(sa.update(t).where(t.c.endpoint == endpoint)
                          .values(keys=vault.seal(plain, aad=_push_aad(endpoint))))
                n += 1
        for key, value in c.execute(sa.select(app_secrets.c.key, app_secrets.c.value)).all():
            if vault.stale(value):
                c.execute(sa.update(app_secrets).where(app_secrets.c.key == key)
                          .values(value=vault.seal(vault.open_(value, aad=key), aad=key)))
                n += 1
    return n


def create_pairing(code: str, poll_secret: str, label: str, ttl_s: int) -> None:
    """A pending pairing; only the hashes of the code and the poll secret are stored."""
    from datetime import timedelta
    now = _utc_now()
    with _write() as c:
        c.execute(sa.insert(espn_pairings).values(
            code_hash=_token_hash(code), poll_hash=_token_hash(poll_secret), label=label, created_at=_iso(now),
            expires_at=_iso(now + timedelta(seconds=ttl_s))))


def open_pairings() -> int:
    """Live pairings nobody has confirmed yet (the ceiling on unauthenticated starts)."""
    t = espn_pairings
    with _read() as c:
        return c.execute(sa.select(sa.func.count()).select_from(t).where(
            t.c.user_id.is_(None), t.c.expires_at > _iso(_utc_now()))).scalar_one()


def confirm_pairing(code: str, user_id: int) -> str | None:
    """Bind a live, unconfirmed pairing to the user; its label, or None when no such code is pending."""
    t = espn_pairings
    now = _iso(_utc_now())
    with _write() as c:
        return c.execute(sa.update(t).where(t.c.code_hash == _token_hash(code), t.c.user_id.is_(None),
                                            t.c.expires_at > now)
                         .values(user_id=user_id, confirmed_at=now).returning(t.c.label)).scalar()


def poll_pairing(poll_secret: str, device_token: str) -> tuple[str, int | None]:
    """("pending", None) until confirmed; ("connected", device id) once, when the confirmed pairing becomes a device
    under `device_token` (stored hashed) and the pairing is deleted; ("expired", None) for an unknown or lapsed one."""
    t = espn_pairings
    now = _iso(_utc_now())
    h = _token_hash(poll_secret)
    with _read() as c:   # anyone may poll: a secret that matches nothing never opens a write
        if c.execute(sa.select(t.c.id).where(t.c.poll_hash == h)).first() is None:
            return "expired", None
    with _write() as c:
        r = c.execute(sa.select(t).where(t.c.poll_hash == h).with_for_update()).first()
        if r is None:
            return "expired", None
        if r.user_id is None:
            return ("pending", None) if r.expires_at > now else ("expired", None)
        device = c.execute(sa.insert(extension_devices).values(
            token_hash=_token_hash(device_token), user_id=r.user_id, label=r.label, created_at=now)
            .returning(extension_devices.c.id)).scalar_one()
        c.execute(sa.delete(t).where(t.c.id == r.id))
        return "connected", device


def sweep_pairings() -> int:
    """Delete every pairing past expires_at, confirmed or not (an extension that never polled pairs again)."""
    with _write() as c:
        return c.execute(sa.delete(espn_pairings).where(espn_pairings.c.expires_at <= _iso(_utc_now()))).rowcount


def device_user(token: str) -> tuple[int, int] | None:
    """(device id, user id) for a paired extension's token, marking it used; None for an unknown or revoked one."""
    t = extension_devices
    h = _token_hash(token)
    with _read() as c:   # anyone may present a token: one that matches nothing never opens a write
        if c.execute(sa.select(t.c.id).where(t.c.token_hash == h)).first() is None:
            return None
    with _write() as c:
        r = c.execute(sa.update(t).where(t.c.token_hash == h).values(last_used_at=_iso(_utc_now()))
                      .returning(t.c.id, t.c.user_id)).first()
    return (r.id, r.user_id) if r else None


def user_devices(user_id: int) -> list[dict]:
    """The user's paired extensions, newest first: id, label, created_at, last_used_at."""
    t = extension_devices
    with _read() as c:
        rows = c.execute(sa.select(t.c.id, t.c.label, t.c.created_at, t.c.last_used_at)
                         .where(t.c.user_id == user_id).order_by(t.c.id.desc())).all()
    return [dict(r._mapping) for r in rows]


def revoke_device(user_id: int, device_id: int) -> bool:
    """Unpair one of the user's extensions; False when they have none by that id."""
    t = extension_devices
    with _write() as c:
        return c.execute(sa.delete(t).where(t.c.user_id == user_id, t.c.id == device_id)).rowcount > 0


def revoke_devices(user_id: int) -> int:
    with _write() as c:
        return c.execute(sa.delete(extension_devices).where(extension_devices.c.user_id == user_id)).rowcount


def delete_user(user_id: int) -> None:
    """The account and every personal row, in one transaction (ffl-soc.6.2.4). The users row's CASCADEs take the
    sessions, memberships (and with them member_state, member_caps) and every `_user()` table; page_cache
    has no FK, so its rows go by hand. A team's rows (claim plans, seats, team_state) are shared with its co-managers:
    they go only for a team nobody else is on. The leagues themselves stay, memberless or not."""
    team_tables = (claim_plans, seats, team_state)
    with _write() as c:
        mine = c.execute(sa.select(memberships.c.league, memberships.c.team_id)
                         .where(memberships.c.user_id == user_id, memberships.c.team_id.isnot(None))).all()
        for league, team_id in mine:
            others = c.execute(sa.select(sa.func.count()).select_from(memberships).where(
                memberships.c.league == league, memberships.c.team_id == team_id,
                memberships.c.user_id != user_id)).scalar_one()
            if not others:
                for t in team_tables:
                    c.execute(sa.delete(t).where(t.c.league == league, t.c.team_id == team_id))
        c.execute(sa.delete(page_cache).where(page_cache.c.user_id == user_id))
        c.execute(sa.delete(users).where(users.c.id == user_id))


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "import":
        from . import db_import
        from .config import JOURNAL
        return db_import.main(JOURNAL, DATA, SEASON)
    if cmd == "backup":
        from . import db_backup
        return db_backup.main(argv[1:])
    if cmd == "restore":
        from . import db_backup
        return db_backup.restore_main(argv[1:])
    if cmd == "pgcopy":
        from . import db_copy
        return db_copy.main(argv[1:])
    if cmd == "counts":
        from . import db_copy
        return db_copy.counts_main(argv[1:])
    print("usage: python -m ffl.db import | backup [dest] | restore <src> --url URL [--yes] "
          "| pgcopy <sqlite file> | counts A [B]")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
