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

HOST_URL = "https://fastcast.semfs.engsvc.go.com/public/websockethost"
PROFILE = 12000
TOPIC = "scoreboard-ffl-fantasycast"
TIMEOUT = 20


# ---- frames -----------------------------------------------------------------------------------

PATCH_OPS = ("R", "P")  # R: replay of recent messages right after subscribing; P: a live push


def decode(text: str) -> dict:
    """A frame as a dict; a patch frame (R or P) gains `patch`, the decoded JSON-Patch op list."""
    f = json.loads(text)
    if f.get("op") in PATCH_OPS:
        inner = json.loads(f["pl"])
        raw = inner["pl"]
        if inner.get("~c"):
            raw = zlib.decompress(base64.b64decode(raw)).decode()
        f["patch"] = json.loads(raw)
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

def scoring_table(settings: dict) -> dict[int, float]:
    """stat_id -> points from the league settings' scoring list (ffl.settings)."""
    return {int(x["stat_id"]): float(x["points"]) for x in settings["scoring"]}


def player_points(stats: dict, table: dict[int, float]) -> float:
    return sum(table.get(int(k), 0.0) * float(v) for k, v in stats.items())


def score_all(doc: dict, table: dict[int, float]) -> dict[int, float]:
    """Every player in the checkpoint scored under the league's table, by ESPN player id."""
    return {int(pid): round(player_points(row.get("stats", {}), table), 2)
            for pid, row in doc.get("statistics", {}).items()}


# ---- game state -------------------------------------------------------------------------------

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

    def __init__(self, on_doc: Callable[[dict], None], topic: str = TOPIC):
        self.on_doc, self.topic = on_doc, topic
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

    def _load_checkpoint(self, mid: int) -> None:
        r = requests.get(self.checkpoint_url(mid), timeout=TIMEOUT)
        r.raise_for_status()
        self.doc, self.mid = r.json(), mid
        self.on_doc(self.doc)

    def _frame(self, text: str) -> None:
        f = decode(text)
        op = f.get("op")
        if op == "H":
            self._load_checkpoint(int(f["mid"]))
        elif op in PATCH_OPS:
            if not self.doc:
                return  # a delta before the checkpoint: nothing to apply it to
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
                stop.wait(delay)
                delay = min(delay * 2, 60.0)
            finally:
                self.connected_at = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
