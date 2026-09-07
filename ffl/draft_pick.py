"""Make picks in the open ESPN draft room.

    uv run python -m ffl.draft_pick 3918298 "Josh Allen"     # draft by ESPN player id
    uv run python -m ffl.draft_pick --autopick-off

Drives the tab you are already drafting in and never opens another — a second draft-room
connection boots you out of your own draft (see ffl/draft_socket.py).

How the room actually works, learned the hard way in a practice draft:
  - The player table is virtualised, so a player you have not scrolled to has no row in the
    DOM. Finding a row by name only works for whoever happens to be rendered.
  - The search box does not filter the table; it opens a dropdown of
    `button.player--search--match` elements carrying `data-player-search-playerid`. That id is
    ESPN's, the same one on our board, so we select by id and never match on name text.
  - Only undrafted players appear in that dropdown (unless "Show Drafted" is on), so a missing
    suggestion means the player is already gone — worth distinguishing from a broken selector.
  - The Draft button is NOT in the player table. Selecting a search result opens a player
    panel (`div.draft-column`) holding `button.Button--draft[data-player-id=...]`, which has
    no table-row ancestor — row-based lookups find nothing. It renders uppercase via a `ttu`
    class while its DOM text is "Draft".
  - Practice drafts open with Autopick ON, and ESPN silently switches it back on after a
    pick times out. While it is on the panel button reads Queue, not Draft, so every pick
    must re-check it. Read the toggle's checked state — the "DISABLE AUTOPICK" banner button
    is missing while the room is still rendering and makes an on room look off.
"""
from __future__ import annotations

import json
import re
import sys
import time

from playwright.sync_api import sync_playwright

from .config import DATA
from .draft_socket import CDP_PORT, DRAFT_URL_MATCH


class PlayerGone(RuntimeError):
    """The player is not offered in the search dropdown — almost always already drafted."""


def _room(ctx):
    pages = [p for p in ctx.pages if not p.is_closed() and DRAFT_URL_MATCH in p.url]
    if not pages:
        raise SystemExit("No draft room tab open.")
    return pages[-1]


AUTOPICK_TOGGLE = "[class*=autoPick-toggle] input"


def autopick_on(page) -> bool:
    """Read the toggle itself, not the banner.

    The "DISABLE AUTOPICK" button is absent while the room is still rendering, so checking for
    it reports "already off" on a room that is very much on — which is how a practice draft
    autopicked our first round out from under us.
    """
    states = page.eval_on_selector_all(AUTOPICK_TOGGLE, "es => es.map(e => e.checked)")
    return bool(states) and bool(states[0])


def autopick_off(page) -> str:
    """Turn autopick off and confirm it took. ESPN re-enables it after a missed pick."""
    if not autopick_on(page):
        return "autopick already off"
    btn = page.get_by_role("button", name="DISABLE AUTOPICK", exact=False)
    if btn.count() and btn.first.is_visible():
        btn.first.evaluate("el => el.click()")
    else:
        page.locator(AUTOPICK_TOGGLE).first.evaluate("el => el.click()")
    page.wait_for_timeout(900)
    if autopick_on(page):
        raise RuntimeError("could not turn autopick off — ESPN will draft for you")
    return "autopick disabled"


def _select(page, player_id: int, name: str):
    """Open one player's panel via the search dropdown."""
    box = page.locator("input[placeholder='Player Name']").first
    box.click()
    box.fill("")
    box.type(name, delay=25)
    page.wait_for_timeout(1200)
    match = page.locator(f'button.player--search--match[data-player-search-playerid="{player_id}"]')
    if not match.count():
        offered = page.eval_on_selector_all(
            "button.player--search--match",
            "es => es.map(e => e.getAttribute('data-player-search-playername'))")
        raise PlayerGone(f"{name} not offered (dropdown had: {offered})")
    match.first.click()
    page.wait_for_timeout(1200)
    return match.first


def pick(player_id: int, name: str, queue: bool = False) -> str:
    want = "QUEUE" if queue else "DRAFT"
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        page = _room(b.contexts[0])
        page.bring_to_front()
        # With autopick on, the panel offers Queue instead of Draft — and ESPN turns it back
        # on by itself after a pick times out, so re-check every time rather than once.
        if not queue:
            autopick_off(page)
        _select(page, player_id, name)
        # The action button is NOT in the player table — it lives in the player panel that the
        # search selection opens (`div.draft-column`), with no `tr` ancestor at all, which is
        # why every row-based lookup found nothing. It carries the ESPN player id, so match on
        # that and never on row text.
        css = f'button.Button--{"queue" if queue else "draft"}[data-player-id="{player_id}"]'
        btn = page.locator(css).first
        try:
            # wait for the panel to actually switch to this player — it lags the suggestion
            # click, and checking once caught the previous player's button instead
            btn.wait_for(state="visible", timeout=6000)
        except Exception:
            visible = page.eval_on_selector_all(
                "button[data-player-id]",
                "es => es.map(e => e.className + '#' + e.getAttribute('data-player-id'))")
            raise RuntimeError(
                f"no {want} button for {name} (are you on the clock?). "
                f"buttons with a player id: {visible}")
        # the sticky column headers overlay the table and swallow a real mouse click, so
        # dispatch it directly on the element instead of hit-testing through the page
        btn.evaluate("el => el.click()")
        page.wait_for_timeout(700)
        for label in ["Draft Player", "Confirm", "Yes"]:
            c = page.get_by_role("button", name=re.compile(rf"^\s*{label}\s*$", re.I))
            if c.count() and c.first.is_visible():
                c.first.evaluate("el => el.click()")
                break
        # Verify against the socket feed, which is ground truth. Checking the DOM instead gave
        # false negatives — a drafted player still shows up in the search dropdown, so two
        # picks that actually landed (Jordan Love, Brandon Aubrey) were reported as failures.
        if queue:
            return f"queued {name}"
        landed = confirm_drafted(player_id)
        if landed is False:
            raise RuntimeError(f"clicked DRAFT on {name} but no pick arrived on the feed")
        return f"drafted {name}" + ("" if landed else " (unconfirmed: no watcher feed)")


def confirm_drafted(player_id: int, timeout_s: float = 8.0):
    """True once the pick shows up on the socket feed; None if no feed is running."""
    path = DATA / "socket_picks.json"
    if not path.exists():
        return None
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if any(p["playerId"] == player_id for p in json.loads(path.read_text())):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


if __name__ == "__main__":
    args = sys.argv[1:]
    with sync_playwright() as _p:
        _b = _p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        _page = _room(_b.contexts[0])
        if "--autopick-off" in args:
            print(autopick_off(_page))
            sys.exit(0)
    print(pick(int(args[0]), args[1], queue="--queue" in args))
