"""Live pick feed from the ESPN draft room's websocket, read over CDP.

Why: ESPN's REST `mDraftDetail` view does NOT update during a live draft — every slot stays
`playerId: -1` until the draft is *finished* (verified in a practice draft 4 rounds deep).
The draft room itself gets picks over a websocket, so we attach to the Chrome tab that
`ffl.browser` already keeps open (CDP :9222) and read its frames. No second login, no
reimplementing ESPN's socket handshake.

ONE CONNECTION PER TEAM. ESPN's draft server kicks the earlier session out on a duplicate
connect — we did this to ourselves in testing by opening a second draft tab, and again with
a standalone websocket client (the token for `wss://fantasydraft.espn.com/...JOIN` is just
`1:<league>:<team>:<SWID>:<code>`, the code coming from the `teams/<id>/draftSecurity`
endpoint — it works, and it boots you out of your own draft). So this feed is strictly
passive: it never opens a tab, never navigates, and never connects a socket of its own.

Frame protocol (plain text, space separated):
    SELECTED <teamId> <playerId> <lineupSlotId> [{memberId}]   a pick was made
    SELECTING <teamId> <msOnClock>                             team is on the clock
    CLOCK <n> <msRemaining> <teamId>                           clock tick
    AUTOSUGGEST <playerId> / AUTODRAFT <teamId> <bool>         room UI hints
    PING / PONG                                                keepalive

Picks arrive in draft order, so overall pick number is just arrival index + 1.

ONE LEAGUE PER FEED. The feed attaches only to a draft tab whose URL names its own league
(`leagueId=<id>`): with two leagues' rooms open, reading the other's picks would mark the wrong
players gone. The picks go to the league's row of the DB's `draft_feeds` as they land, so a
restarted watcher resumes where the last one stopped.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from . import db
from .config import CDP_PORT
DRAFT_URL_MATCH = "/football/draft"


def is_room(url: str, league_id: str) -> bool:
    """True for the draft room of league `league_id` (the query's leagueId exactly, never a prefix of it)."""
    u = urlparse(url)
    return DRAFT_URL_MATCH in u.path and parse_qs(u.query).get("leagueId") == [str(league_id)]


def room_page(pages, league_id: str):
    """The open draft tab of league `league_id` (the newest if there are two), or None."""
    rooms = [p for p in pages if not p.is_closed() and is_room(p.url, league_id)]
    return rooms[-1] if rooms else None


class FeedStore:
    """Where a feed keeps its picks: the league's `draft_feeds` row."""

    def __init__(self, ref):
        self.ref = ref

    def load(self) -> tuple[list[dict], int]:
        return db.draft_feed(self.ref)

    def save(self, picks: list[dict], offset: int) -> None:
        db.put_draft_feed(self.ref, picks, offset)


class DraftFeed:
    """Accumulates picks off one league's draft room socket. Use as a context manager and call pump().

    store: a FeedStore (load/save); None keeps the picks in memory only."""

    def __init__(self, league_id: str, store: FeedStore | None = None):
        self.picks: list[dict] = []          # [{teamId, playerId, lineupSlotId, overall}]
        self.on_clock: int | None = None
        # how many picks had been made when that SELECTING arrived: the frame lags the pick
        # that ended the previous turn, so "who is on the clock" is only trustworthy while
        # this still matches len(self.picks) (see on_clock_now)
        self.on_clock_after = 0
        self.ms_left: int | None = None
        self.league_id = str(league_id)
        self.store = store
        self.url = None
        self.offset = 0  # picks made before we attached (see sync_offset)
        self._pw = self._browser = self._page = self._cdp = None
        self.dirty = False  # picks the store has not taken yet (see _save)
        if store:  # resume after a crash/restart mid-draft
            self.picks, self.offset = store.load()

    def _save(self):
        """Write the picks through to the store. A DB error is one line on screen and a dirty flag: the next
        pump() tries again, so the stored feed (what advise, avail and autodraft read) never lags in silence."""
        if not self.store:
            return
        try:
            self.store.save(self.picks, self.offset)
            self.dirty = False
        except db.DBError as e:
            if not self.dirty:
                print(f"  !! pick feed not saved to the DB ({e.__class__.__name__}); retrying", flush=True)
            self.dirty = True

    # -- lifecycle -------------------------------------------------------
    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self.url = self.connect()
        return self

    def __exit__(self, *exc):
        self._stop_pw()

    def _stop_pw(self):
        try:
            self._pw.stop()
        except Exception:
            pass
        self._pw = None

    def reconnect(self):
        """Full restart after the CDP link dies.

        A page reload kills the playwright driver process outright, and reusing the dead
        driver hangs forever (this silently stalled a test watcher at pick 22), so tear the
        whole thing down and start a fresh driver.
        """
        from playwright.sync_api import sync_playwright

        self._stop_pw()
        self._pw = sync_playwright().start()
        self.url = self.connect()
        return self.url

    def connect(self):
        """(Re)attach to the draft tab. Safe to call again after the CDP link drops."""
        self._browser = self._pw.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        ctx = self._browser.contexts[0]
        page = room_page(ctx.pages, self.league_id)
        if page is None:
            raise RuntimeError(
                f"No draft room tab open for league {self.league_id} (looking for '{DRAFT_URL_MATCH}' "
                f"with leagueId={self.league_id}). Run `uv run python -m ffl.browser` and open the draft room first."
            )
        self._page = page
        # A page dialog (ESPN confirm/alert) with no handler makes the driver auto-dismiss it;
        # if the dialog is already gone by then the driver process dies with an uncaught
        # ProtocolError (live draft 2026-09-07, 18:33). Handle it ourselves and swallow errors.
        self._page.on("dialog", self._on_dialog)
        self._cdp = ctx.new_cdp_session(self._page)
        self._cdp.on("Network.webSocketFrameReceived", self._on_frame)
        self._cdp.send("Network.enable")
        return self._page.url

    @staticmethod
    def _on_dialog(dialog):
        try:
            print(f"  page dialog ({dialog.type}): {dialog.message!r} — dismissed", flush=True)
            dialog.dismiss()
        except Exception:
            pass

    # -- frames ----------------------------------------------------------
    def _on_frame(self, ev):
        try:
            self._handle(ev["response"]["payloadData"])
        except Exception:
            pass  # never let a malformed frame kill the watcher mid-draft

    def _handle(self, payload: str):
        parts = payload.split()
        if not parts:
            return
        kind = parts[0]
        if kind == "SELECTED" and len(parts) >= 4:
            pick = {"teamId": int(parts[1]), "playerId": int(parts[2]),
                    "lineupSlotId": int(parts[3]), "overall": len(self.picks) + 1}
            if any(p["playerId"] == pick["playerId"] for p in self.picks):
                return  # duplicate frame (reconnect replay)
            self.picks.append(pick)
            self._save()
        elif kind == "SELECTING" and len(parts) >= 3:
            self.on_clock, self.ms_left = int(parts[1]), int(parts[2])
            self.on_clock_after = len(self.picks)
        elif kind == "CLOCK" and len(parts) >= 4:
            self.ms_left, self.on_clock = int(parts[2]), int(parts[3])
            self.on_clock_after = len(self.picks)

    def on_clock_now(self) -> int | None:
        """The team on the clock, or None if the socket has not caught up with the last pick.

        SELECTING arrives a beat after the pick that ended the previous turn, so between the
        two the last frame still names whoever just picked. Reporting that as "on the clock"
        told us it was our turn one pick too long — a driver then tried to draft out of turn
        (seen in practice draft 3 at pick 15). None makes the caller fall back to the snake
        order, which is right by construction.
        """
        return self.on_clock if self.on_clock_after == len(self.picks) else None

    def sync_offset(self) -> int:
        """Count picks made before we attached, from the room's own "ON THE CLOCK: PICK n".

        Overall pick numbers are derived from the order frames arrive, so attaching even a
        few seconds late silently shifts every round/pick number AND leaves those players
        marked available — the board then recommends someone already drafted. Observed live:
        attached 3 picks late, and the board offered Josh Allen at every turn for the rest of
        the draft because his pick was never seen.
        """
        import re

        try:
            txt = self._page.locator("[class*=current-pick-module], [class*=pickTrain]").first.inner_text()
        except Exception:
            return self.offset
        m = re.search(r"PICK\s+(\d+)", txt, re.I)
        if not m:
            return self.offset
        made = int(m.group(1)) - 1  # the pick on the clock has not happened yet
        self.offset = max(made - len(self.picks), 0)
        self._save()
        return self.offset

    def backfill(self, name_to_id: dict[str, int]) -> list[str]:
        """Recover picks made before we attached, from the room's always-rendered Picks rail.

        Each entry reads "<Player> / <TEAM> <POS>" then "R<r>, P<p> - <team name>", so the
        rail is a complete pick log we can read without clicking anything in the user's room
        (switching them to the Pick History tab mid-draft would yank their view).

        Only fills in ahead of what we have seen: socket frames stay authoritative.
        """
        if not self.offset:
            return []
        try:
            entries = self._page.eval_on_selector_all(
                "[class*=pick-message__container], [class*=picks] li",
                "es => es.map(e => e.textContent.trim())")
        except Exception:
            return []
        found, unmatched = [], []
        seen_ids = {p["playerId"] for p in self.picks}
        for text in entries:
            player = text.split("/")[0].strip()
            pid = name_to_id.get(player.lower())
            if pid is None:
                unmatched.append(player)
                continue
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            found.append({"teamId": 0, "playerId": pid, "lineupSlotId": -1,
                          "overall": len(found) + 1, "backfilled": True})
        if not found:
            return unmatched
        # earliest first, then everything the socket already gave us
        self.picks = found[: self.offset] + self.picks
        self.offset = max(self.offset - len(found), 0)
        self._save()
        return [f["playerId"] for f in found]

    def pump(self, ms: int = 1000) -> bool:
        """Let queued socket events dispatch. Returns False if the CDP link died."""
        try:
            self._page.wait_for_timeout(ms)
        except Exception:
            return False
        if self.dirty:
            self._save()
        return True
