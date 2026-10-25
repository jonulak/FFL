"""ESPN's live fantasy stat feed (FastCast), the source for seconds-level scores in the UI.

The fantasy boxscore page does not poll for scores: it opens a FastCast pubsub websocket and
subscribes to `scoreboard-ffl-fantasycast`. The server answers with a checkpoint URL on ESPN's
public CDN — every NFL game of the week (status, clock, last play) plus every player's raw
stats keyed by the same stat ids the league's scoring settings use — and then streams
JSON-Patch deltas against that document, zlib-compressed and base64'd, as plays are scored.
Nothing here needs the league cookies. Found by tapping the page over CDP on 2026-09-13;
summing `points[stat_id] * stats[stat_id]` reproduced ESPN's box score exactly.

Protocol (all frames are JSON text):
    -> {"op":"C"}                              <- {"op":"C","sid":..,"hbi":30}
    -> {"op":"S","sid":..,"tc":TOPIC}          <- {"op":"S",..}
                                               <- {"op":"H","mid":N,"pl":"<checkpoint url>"}
                                               <- {"op":"R","mid":N,"pl":"{ts,~c,pl:<b64 zlib patch>}"}  (replay)
                                               <- {"op":"P","mid":N,"pl":...}   (a live patch, every few seconds in-game)
                                               <- {"op":"I","mid":N,"pl":"0"}   (idle marker)
                                               <- {"op":"B"}   (heartbeat, ~10 s; nothing to send back)

The pure half (decode, apply_patch, scoring, game state) is what the tests cover; `Client`
is the socket loop and is exercised only against the real feed.
"""
from __future__ import annotations

import base64
import copy
import json
import threading
import time
import zlib
from collections.abc import Callable
from datetime import datetime, timezone

import requests

from . import formats

HOST_URL = "https://fastcast.semfs.engsvc.go.com/public/websockethost"
PROFILE = 12000
TOPIC = "scoreboard-ffl-fantasycast"
TIMEOUT = 20


# ---- frames -----------------------------------------------------------------------------------

PATCH_OPS = ("R", "P")  # R: replay of recent messages right after subscribing; P: a live push


def decode(text: str) -> dict:
    """A frame as a dict; a patch frame (R or P) gains `patch`, the decoded JSON-Patch op list.

    A patch too big to push comes as {useCDN, eventId, edgeUrl}, edgeUrl the whole document: the
    frame gains `checkpoint`, that URL, for `Client._frame` to reload (seen 2026-10-25).
    A patch frame with no payload, outer or inner, gains no `patch`: there is nothing to apply,
    and `Client._frame` skips it rather than reconnect (seen 2026-09-17 and 2026-09-28)."""
    f = json.loads(text)
    if f.get("op") in PATCH_OPS:
        inner = f.get("pl")
        inner = json.loads(inner) if isinstance(inner, str) else inner
        if isinstance(inner, dict) and inner.get("useCDN") and inner.get("edgeUrl"):
            f["checkpoint"] = inner["edgeUrl"]
            return f
        if not isinstance(inner, dict) or "pl" not in inner:
            return f
        raw = inner["pl"]
        if inner.get("~c"):
            raw = zlib.decompress(base64.b64decode(raw)).decode()
        # an uncompressed payload is sometimes a JSON string, sometimes the op list inlined
        f["patch"] = json.loads(raw) if isinstance(raw, str) else raw
        f["ts"] = inner.get("ts")
    return f


def _unescape(seg: str) -> str:
    return seg.replace("~1", "/").replace("~0", "~")


def _walk(doc, segs: list[str]):
    """The container the last path segment addresses, and that segment (list index as int)."""
    cur = doc
    for s in segs[:-1]:
        cur = cur[int(s)] if isinstance(cur, list) else cur[s]
    last = segs[-1]
    if isinstance(cur, list):
        return cur, (len(cur) if last == "-" else int(last))
    return cur, last


def _get(doc, path: str):
    cur, key = _walk(doc, [_unescape(s) for s in path.split("/")[1:]])
    return cur[key]


def apply_patch(doc, ops: list[dict]) -> None:
    """Apply a JSON-Patch (add / replace / remove / copy / move) in place, all or nothing.

    ESPN emits replace and add for stats and game state, copy when a stat line is duplicated
    (a D/ST row mirrors its team), and the odd remove. A failure raises after restoring the
    document, so a caller never keeps a half-applied patch; it should resync.
    """
    snapshot = copy.deepcopy(doc)
    try:
        for op in ops:
            kind = op.get("op")
            if kind not in ("add", "replace", "remove", "copy", "move"):
                raise ValueError(f"unsupported JSON-Patch op {kind!r}")
            if kind in ("copy", "move"):
                value = copy.deepcopy(_get(doc, op["from"]))
                if kind == "move":
                    src, skey = _walk(doc, [_unescape(s) for s in op["from"].split("/")[1:]])
                    del src[skey]
                kind = "add"
            else:
                value = op.get("value")
            segs = [_unescape(s) for s in op["path"].split("/")[1:]]
            cur, key = _walk(doc, segs)
            if kind == "remove":
                del cur[key]
            elif isinstance(cur, list) and kind == "add":
                cur.insert(key, value)
            else:
                if kind == "replace" and not (isinstance(cur, list) or key in cur):
                    raise KeyError(op["path"])
                cur[key] = value
    except Exception:
        doc.clear()
        doc.update(snapshot)
        raise


# ---- scoring ----------------------------------------------------------------------------------

POSITION_IDS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}   # the checkpoint's positionId


def scoring_table(settings: dict) -> dict[str, dict[int, float]]:
    """{pos: {stat_id: points}} (and "" for a player of no known position) from the league's raw scoring
    items, every position's override kept (formats.stat_tables, as ESPN scores its box score); settings with
    no raw items (imported from settings.json, or a hand-built dict) score the flat `scoring` list at every
    position, as before."""
    if settings.get("scoring_items"):
        return formats.stat_tables(settings["scoring_items"])
    flat = {int(x["stat_id"]): float(x["points"]) for x in settings["scoring"]}
    return {pos: flat for pos in ("", *formats.POSITIONS)}


def position_table(table: dict[str, dict[int, float]], pos: str | None) -> dict[int, float]:
    return table.get(pos or "") or table[""]


def player_points(stats: dict, table: dict[int, float]) -> float:
    """One player's points under his position's table (`position_table`)."""
    return sum(table.get(int(k), 0.0) * float(v) for k, v in stats.items())


def row_position(pid, row: dict) -> str | None:
    """A checkpoint row's position: its positionId, else a D/ST by its negative id."""
    try:
        pos = POSITION_IDS.get(int(row.get("positionId") or 0))
    except (TypeError, ValueError):
        pos = None
    return pos or ("D/ST" if int(pid) < 0 else None)


def score_all(doc: dict, table: dict[str, dict[int, float]]) -> dict[int, float]:
    """Every player in the checkpoint scored under the league's table for his position, by ESPN player id."""
    return {int(pid): round(player_points(row.get("stats", {}), position_table(table, row_position(pid, row))), 2)
            for pid, row in doc.get("statistics", {}).items()}


# ---- game state -------------------------------------------------------------------------------

def game_states(doc: dict) -> dict[str, dict]:
    """team abbreviation -> {status: pre|in|post, quarter, clock, score, and while it is on `ball` and
    `red_zone` (`possession`)} for every game in the checkpoint.

    Keyed by team because the page's players carry a pro team, not a game id; a player without
    a stat line yet (a kicker before his first kick) still finds his game this way.
    """
    out: dict[str, dict] = {}
    for e in doc.get("events", []):
        full = e.get("fullStatus") or {}
        teams = [c for c in e.get("competitors", []) if c.get("abbreviation")]
        state = {"status": (full.get("type") or {}).get("state") or e.get("status"),
                 "quarter": full.get("period"), "clock": full.get("displayClock"),
                 "score": {c["abbreviation"]: _score(c.get("score")) for c in teams if _score(c.get("score")) is not None}}
        state.update(possession(e))
        for c in teams:
            out[c["abbreviation"]] = state
    return out


RED_ZONE = 20  # yards to the end zone


def possession(event: dict) -> dict:
    """{ball: team abbreviation, red_zone: bool} while a game is on and someone has the ball, else {}.

    The feed names no personnel, so this is all "on the field" can be read from: the team that
    has the ball after the last play (`lastPlay.end`, the spot of the next snap), else the
    current drive's team; red zone is the ball inside the opponent's 20. Halftime has no ball.
    """
    full = event.get("fullStatus") or {}
    kind = full.get("type") or {}
    if (kind.get("state") or event.get("status")) != "in" or kind.get("name") == "STATUS_HALFTIME":
        return {}
    abbr = {str(c["id"]): c["abbreviation"] for c in event.get("competitors", []) if c.get("id") is not None and c.get("abbreviation")}
    end = (event.get("lastPlay") or {}).get("end") or {}
    drive = event.get("drive") or {}
    team = abbr.get(str(end.get("team")))
    if team is not None and isinstance(end.get("yardsToEndzone"), (int, float)):
        return {"ball": team, "red_zone": end["yardsToEndzone"] <= RED_ZONE}
    team = abbr.get(str(drive.get("teamId")))
    return {"ball": team, "red_zone": bool(drive.get("isRedZone"))} if team else {}


def _score(raw) -> int | None:
    """A competitor's score: a number once the game is on, "" (or absent) before kickoff."""
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def player_stats(doc: dict) -> dict[int, dict]:
    """Every player's raw stat row (stat id -> value), by ESPN player id."""
    return {int(pid): dict(row.get("stats", {})) for pid, row in doc.get("statistics", {}).items()}


def games_in_progress(doc: dict) -> bool:
    return any(e.get("status") == "in" for e in doc.get("events", []))


def next_kickoff(doc: dict, now: datetime) -> datetime | None:
    """The earliest kickoff still ahead among games not yet started."""
    ahead = []
    for e in doc.get("events", []):
        if e.get("status") != "pre":
            continue
        try:
            t = datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if t > now:
            ahead.append(t)
    return min(ahead) if ahead else None


# ---- the socket -------------------------------------------------------------------------------

class Client:
    """One subscription to TOPIC. `on_doc(doc)` fires after the checkpoint loads and after every
    patch; `doc` is the live document (mutated in place, so callers copy what they keep).

    run() blocks until `stop` is set, reconnecting with backoff after any failure, a patch that
    does not apply cleanly included (the reconnect brings a fresh checkpoint).
    """

    def __init__(self, on_doc: Callable[[dict], None], topic: str = TOPIC,
                 on_error: Callable[[str], None] | None = None):
        self.on_doc, self.topic, self.on_error = on_doc, topic, on_error
        self.doc: dict = {}
        self.mid: int | None = None
        self.connected_at: float | None = None
        self.last_error: str | None = None

    @staticmethod
    def host() -> str:
        h = requests.get(HOST_URL, timeout=TIMEOUT).json()
        return f"wss://{h['ip']}:{h['securePort']}/FastcastService/pubsub/profiles/{PROFILE}?TrafficManager-Token={h['token']}"

    def checkpoint_url(self, mid: int) -> str:
        return f"https://fcast.espncdn.com/FastcastService/pubsub/profiles/{PROFILE}/topic/{self.topic}/message/{mid}/checkpoint"

    def _load_checkpoint(self, mid: int, url: str | None = None) -> None:
        r = requests.get(url or self.checkpoint_url(mid), timeout=TIMEOUT)
        r.raise_for_status()
        self.doc, self.mid = r.json(), mid
        self.last_error = None  # a fresh document: whatever failed before is repaired
        self.on_doc(self.doc)

    def _frame(self, text: str) -> None:
        f = decode(text)
        op = f.get("op")
        if op == "H":
            self._load_checkpoint(int(f["mid"]))
        elif op in PATCH_OPS:
            if not self.doc:
                return  # a delta before the checkpoint: nothing to apply it to
            if "checkpoint" in f:
                # the patch was too big to push: the document it leaves is on the CDN
                self._load_checkpoint(int(f["mid"]), f["checkpoint"])
                return
            if "patch" not in f:
                # no payload: nothing to apply. Log its shape, since none has been captured yet
                print(f"fastcast: skipped a patch frame without a payload: {text[:500]}")
                return
            # ESPN publishes a checkpoint only every so often, so a failed patch cannot be
            # repaired from the checkpoint at this mid: raise, and run() reconnects, which
            # hands us a fresh H frame naming a checkpoint that exists.
            apply_patch(self.doc, f["patch"])
            self.mid = int(f["mid"])
            self.on_doc(self.doc)

    def run_once(self, stop: threading.Event) -> None:
        """One connection, until the socket closes or `stop` is set."""
        from websockets.sync.client import connect

        with connect(self.host(), open_timeout=TIMEOUT) as ws:
            ws.send(json.dumps({"op": "C"}))
            hello = json.loads(ws.recv(timeout=TIMEOUT))
            ws.send(json.dumps({"op": "S", "sid": hello["sid"], "tc": self.topic}))
            self.connected_at = time.time()
            while not stop.is_set():
                try:
                    msg = ws.recv(timeout=5)
                except TimeoutError:
                    continue
                self._frame(msg)

    def run(self, stop: threading.Event) -> None:
        delay = 2.0
        while not stop.is_set():
            try:
                self.run_once(stop)
                delay = 2.0
            except Exception as e:  # noqa: BLE001 — any socket/HTTP failure: back off and reconnect
                self.last_error = f"{type(e).__name__}: {e}"
                print(f"fastcast: {self.last_error}")  # the API log keeps it; the status forgets on repair
                if self.on_error:
                    self.on_error(self.last_error)
                stop.wait(delay)
                delay = min(delay * 2, 60.0)
            finally:
                self.connected_at = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
