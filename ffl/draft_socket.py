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
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import CDP_PORT
DRAFT_URL_MATCH = "/football/draft"


class DraftFeed:
    """Accumulates picks off the draft room socket. Use as a context manager and call pump()."""

    def __init__(self, cache: Path | None = None, url_match: str = DRAFT_URL_MATCH):
        self.picks: list[dict] = []          # [{teamId, playerId, lineupSlotId, overall}]
        self.on_clock: int | None = None
        self.ms_left: int | None = None
        self.url_match = url_match
        self.cache = cache
        self.url = None
        self.offset = 0  # picks made before we attached (see sync_offset)
        self._pw = self._browser = self._page = self._cdp = None
        if cache and cache.exists():  # resume after a crash/restart mid-draft
            self.picks = json.loads(cache.read_text())

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
        pages = [p for p in ctx.pages if not p.is_closed() and self.url_match in p.url]
        if not pages:
            raise RuntimeError(
                f"No draft room tab open (looking for '{self.url_match}'). "
                "Run `uv run python -m ffl.browser` and open the draft room first."
            )
        self._page = pages[-1]
        self._cdp = ctx.new_cdp_session(self._page)
        self._cdp.on("Network.webSocketFrameReceived", self._on_frame)
        self._cdp.send("Network.enable")
        return self._page.url

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
            if self.cache:
                self.cache.write_text(json.dumps(self.picks))
        elif kind == "SELECTING" and len(parts) >= 3:
            self.on_clock, self.ms_left = int(parts[1]), int(parts[2])
        elif kind == "CLOCK" and len(parts) >= 4:
            self.ms_left, self.on_clock = int(parts[2]), int(parts[3])

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
        if self.cache:
            self.cache.write_text(json.dumps(self.picks))
        return [f["playerId"] for f in found]

    def pump(self, ms: int = 1000) -> bool:
        """Let queued socket events dispatch. Returns False if the CDP link died."""
        try:
            self._page.wait_for_timeout(ms)
            return True
        except Exception:
            return False
