"""Make picks in the open ESPN draft room.

    uv run python -m ffl.draft_pick 3918298 "Josh Allen"     # draft by ESPN player id
    uv run python -m ffl.draft_pick --autopick-off

Drives the tab you are already drafting in and never opens another — a second draft-room
connection boots you out of your own draft (see ffl/draft_socket.py).

How the room actually works, learned the hard way in a practice draft:
  - The player table is virtualised, so a player you have not scrolled to has no row in the
    DOM. Finding a row by name only works for whoever happens to be rendered.
  - Typing in the search box does not filter the table by itself; it opens a dropdown of
    `button.player--search--match` elements carrying `data-player-search-playerid`. That id is
    ESPN's, the same one on our board, so we select by id and never match on name text.
    *Clicking* a suggestion filters the table down to that one player.
  - Only undrafted players appear in that dropdown (unless "Show Drafted" is on), so a missing
    suggestion means the player is already gone — worth distinguishing from a broken selector.
  - The action button lives on the player's own table row and carries his id, so match on
    `[data-player-id]` and never on row text. Three things can occupy that cell:
    `Button--draft` ("Draft", we are on the clock with autopick off), `Button--queue`
    ("Queue", autopick is on or we are not on the clock), or the bare text "DRAFTED" with no
    button at all (he is gone). There is also one `Button--draft` outside the table, on the
    "your autopick would be X" panel — it drafts ESPN's suggestion, not our man, which is why
    the id has to match.
  - The Draft button ignores clicks for its first ~3 seconds (ESPN runs a countdown on it) and
    reports nothing, so a single click is not enough: click until the feed confirms.
  - Practice drafts open with Autopick ON, and ESPN switches it back on after every pick that
    times out. While it is on every row reads Queue, which is what a practice draft's worth of
    "no DRAFT button" failures turned out to be. Read the toggle's checked state — the
    "DISABLE AUTOPICK" banner button is missing while the room is still rendering and makes an
    on room look off — and treat "toggle not rendered" as unknown, not as off.
"""
from __future__ import annotations

import re
import sys
import time

from playwright.sync_api import sync_playwright

from . import db, leagues
from .draft_socket import CDP_PORT, room_page


class PlayerGone(RuntimeError):
    """The player is not offered in the search dropdown — almost always already drafted."""


def _room(ctx, ref):
    """The draft tab of `ref`'s league, never another league's room (a click there drafts in the wrong league)."""
    page = room_page(ctx.pages, ref.provider_id)
    if page is None:
        raise SystemExit(f"No draft room tab open for league {ref.provider_id}.")
    return page


AUTOPICK_TOGGLE = "[class*=autoPick-toggle] input"


def autopick_on(page) -> bool | None:
    """Read the toggle itself, not the banner. None when the toggle is not in the DOM yet.

    The "DISABLE AUTOPICK" button is absent while the room is still rendering, so checking for
    it reports "already off" on a room that is very much on — which is how a practice draft
    autopicked our first round out from under us. "Not rendered" is not "off" either, so an
    empty match returns None and the caller has to decide, rather than getting a cheerful False.

    Note the selector matches two inputs (ESPN reuses the class on the sound control); the
    autopick toggle is the first, the one whose container is labelled "Autopick".
    """
    states = page.eval_on_selector_all(AUTOPICK_TOGGLE, "es => es.map(e => e.checked)")
    return bool(states[0]) if states else None


def autopick_off(page) -> str:
    """Turn autopick off and confirm it took.

    ESPN re-enables autopick every time a pick times out, so this runs before *every* pick,
    not once at the start of the draft. While it is on, the action cell on every player row
    reads Queue instead of Draft — which is what a whole practice draft's worth of "no DRAFT
    button" failures turned out to be.
    """
    state = autopick_on(page)
    if state is False:
        return "autopick already off"
    banner = page.get_by_role("button", name="DISABLE AUTOPICK", exact=False)
    if banner.count() and banner.first.is_visible():
        banner.first.evaluate("el => el.click()")
    elif state is None:
        # toggle not rendered and no banner: nothing to click and nothing to verify, so say
        # so rather than reporting success on a room that may be drafting for us
        return "autopick state unknown (toggle not rendered)"
    else:
        page.locator(AUTOPICK_TOGGLE).first.evaluate("el => el.click()")
    page.wait_for_timeout(900)
    if autopick_on(page):
        raise RuntimeError("could not turn autopick off — ESPN will draft for you")
    return "autopick disabled"


def _row_text(page) -> list[str]:
    return page.eval_on_selector_all(
        ".fixedDataTableRowLayout_rowWrapper",
        "es => es.map(e => e.innerText.replace(/\\n/g, ' ')).filter(t => t.trim())")


def _select(page, player_id: int, name: str):
    """Filter the player table down to one player, via the search box.

    The search box does not filter the table as you type — it opens a dropdown of
    `button.player--search--match` elements carrying `data-player-search-playerid` (ESPN's id,
    the same one on our board, so we select by id and never on name text). *Clicking* one of
    those filters the table to that single player, and his row carries the action button we
    want. Only undrafted players are offered in the dropdown.
    """
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
    # dispatch on the element: the player table overlays the dropdown, so a real mouse click
    # is intercepted by public_fixedDataTableCell_cellContent and hangs until it times out
    match.first.evaluate("el => el.click()")
    page.wait_for_timeout(1200)
    return match.first


def _action(page, player_id: int, name: str, want: str, deadline: float):
    """The player's own button on his (now filtered) table row, once it says what we want.

    Three things can be in that action cell, and telling them apart is the whole game:
      - "Draft"  — we are on the clock with autopick off: what we are after.
      - "Queue"  — autopick is on, or we are not on the clock. Turning autopick off flips it.
      - "DRAFTED" as plain text, no button — he is gone; the row renders but offers nothing.
    """
    css = f'button.Button--{want.lower()}[data-player-id="{player_id}"]'
    while time.time() < deadline:
        btn = page.locator(css)
        if btn.count() and btn.first.is_visible():
            return btn.first
        rows = _row_text(page)
        if any("DRAFTED" in r for r in rows if name.split()[-1] in r):
            raise PlayerGone(f"{name} is already drafted (his row reads DRAFTED)")
        if page.locator(f'button.Button--queue[data-player-id="{player_id}"]').count():
            # his row exists but offers Queue: autopick came back on between our check and now
            autopick_off(page)
        page.wait_for_timeout(400)
    offered = page.eval_on_selector_all(
        "button[data-player-id]",
        "es => es.map(e => e.textContent.trim() + ' ' + e.getAttribute('data-player-id'))")
    raise RuntimeError(
        f"no {want} button for {name} (are you on the clock?). "
        f"autopick={autopick_on(page)} rows={_row_text(page)[:3]} buttons={offered}")


PICK_TIMEOUT_S = 20.0


def pick(player_id: int, name: str, queue: bool = False, timeout_s: float = PICK_TIMEOUT_S, *, ref) -> str:
    """Draft (or queue) one player in `ref`'s room, and do not report success
    unless the feed says so.

    The clock is the budget: a practice room gives 30s and our league 90s, so everything here
    retries against a deadline rather than failing on the first miss.
    """
    want = "QUEUE" if queue else "DRAFT"
    deadline = time.time() + timeout_s
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        page = _room(b.contexts[0], ref)
        # never bring_to_front(): the room does not need focus for these clicks, and
        # stealing it yanks the user's screen away mid-draft
        # With autopick on, every row offers Queue instead of Draft — and ESPN turns it back
        # on by itself after a pick times out, so re-check every time rather than once.
        if not queue:
            autopick_off(page)
        # The filtered row sometimes renders with the *previous* player's action button still
        # in the column (seen on Jordan Mason: his row was there, the only Draft button on the
        # page belonged to someone else, and waiting never fixed it). Re-running the search
        # shakes it loose, so give each selection a short budget and select again if it does
        # not produce our button.
        btn = None
        while btn is None:
            _select(page, player_id, name)
            try:
                btn = _action(page, player_id, name, want, min(deadline, time.time() + 4))
            except RuntimeError:
                if time.time() >= deadline:
                    raise
        # The DRAFT button ignores clicks for the first ~3 seconds of the clock (ESPN runs a
        # countdown on it) and says nothing about it, so one click is not enough: keep clicking
        # until the pick shows up on the feed or the clock runs out.
        last = None
        while time.time() < deadline:
            # the sticky column headers overlay the table and swallow a real mouse click, so
            # dispatch it directly on the element instead of hit-testing through the page
            btn.evaluate("el => el.click()")
            page.wait_for_timeout(500)
            for label in ["Draft Player", "Confirm", "Yes"]:
                c = page.get_by_role("button", name=re.compile(rf"^\s*{label}\s*$", re.I))
                if c.count() and c.first.is_visible():
                    c.first.evaluate("el => el.click()")
                    break
            if queue:
                return f"queued {name}"
            # Verify against the socket feed, which is ground truth. Checking the DOM instead
            # gave false negatives — a drafted player still shows up in the search dropdown, so
            # two picks that actually landed (Jordan Love, Brandon Aubrey) were reported failed.
            last = confirm_drafted(player_id, timeout_s=2.0, ref=ref)
            if last is not False:  # True, or None when no watcher feed is running
                return f"drafted {name}" + ("" if last else " (unconfirmed: no watcher feed)")
        raise RuntimeError(f"clicked {want} on {name} until the clock ran out but no pick "
                           f"arrived on the feed")


def confirm_drafted(player_id: int, timeout_s: float = 8.0, *, ref):
    """True once the pick shows up on the league's socket feed (the DB's draft_feeds); None if no feed is running
    (no pick on it yet, as the old feed file did not exist before the first)."""
    seen_feed = False
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:  # a busy DB is "not yet", never an error out of pick()
            feed = db.draft_feed(ref)[0]
        except db.DBError:
            feed = None
        if feed is not None:
            if not feed and not seen_feed:
                return None
            seen_feed = True
            if any(p["playerId"] == player_id for p in feed):
                return True
        time.sleep(0.5)
    return False


if __name__ == "__main__":
    args = sys.argv[1:]
    with sync_playwright() as _p:
        _b = _p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        _ref = leagues.acting()
        _page = _room(_b.contexts[0], _ref)
        if "--autopick-off" in args:
            print(autopick_off(_page))
            sys.exit(0)
    print(pick(int(args[0]), args[1], queue="--queue" in args, ref=_ref))
