"""Make picks in the open ESPN draft room. EXPERIMENTAL — DOES NOT WORK YET.

Status: in a 17-round practice draft every one of 17 attempts failed and ESPN autodrafted
on clock expiry. Clicking a search suggestion does not put the player into the players
table the way this assumes: the final failures were "0 matching row(s)". Do not rely on
this tomorrow — draft by hand.

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
  - Rows show QUEUE normally and DRAFT only while you are on the clock.
  - Practice drafts open with Autopick ON. Leave it on and ESPN drafts for you.
"""
from __future__ import annotations

import re
import sys

from playwright.sync_api import sync_playwright

from .draft_socket import CDP_PORT, DRAFT_URL_MATCH


class PlayerGone(RuntimeError):
    """The player is not offered in the search dropdown — almost always already drafted."""


def _room(ctx):
    pages = [p for p in ctx.pages if not p.is_closed() and DRAFT_URL_MATCH in p.url]
    if not pages:
        raise SystemExit("No draft room tab open.")
    return pages[-1]


def autopick_off(page) -> str:
    btn = page.get_by_role("button", name="DISABLE AUTOPICK", exact=False)
    if btn.count() and btn.first.is_visible():
        btn.first.click()
        page.wait_for_timeout(800)
        return "autopick disabled"
    return "autopick already off"


def _select(page, player_id: int, name: str):
    """Surface one player via the search dropdown; returns his row in the table."""
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
    return page.locator("tr").filter(has_text=name)


def pick(player_id: int, name: str, queue: bool = False) -> str:
    want = "QUEUE" if queue else "DRAFT"
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        page = _room(b.contexts[0])
        page.bring_to_front()
        rows = _select(page, player_id, name)
        # The name matches rows in several tables (roster panel, pick queue, the player list),
        # and only the player-list row carries the action button — so take the first row that
        # actually has one. Whole-string but case-insensitive: the label renders uppercase via
        # CSS while the DOM text is "Draft", so exact="DRAFT" matches nothing, while a loose
        # substring match grabs "Draft Help" in the header and silently clicks that instead.
        label = re.compile(rf"^\s*{want}\s*$", re.I)
        btn = None
        for i in range(rows.count()):
            candidate = rows.nth(i).get_by_role("button", name=label)
            if candidate.count():
                btn = candidate.first
                break
        if btn is None:
            seen = [rows.nth(i).locator("button").all_inner_texts() for i in range(rows.count())]
            raise RuntimeError(
                f"no {want} button for {name} (are you on the clock?). "
                f"{rows.count()} matching row(s), buttons={seen}")
        btn.wait_for(state="visible", timeout=6000)
        # the sticky column headers overlay the table and swallow a real mouse click, so
        # dispatch it directly on the element instead of hit-testing through the page
        btn.evaluate("el => el.click()")
        page.wait_for_timeout(700)
        for label in ["Draft Player", "Confirm", "Yes"]:
            c = page.get_by_role("button", name=re.compile(rf"^\s*{label}\s*$", re.I))
            if c.count() and c.first.is_visible():
                c.first.evaluate("el => el.click()")
                break
        # Verify rather than assume: the room drops the player off the available list once he
        # is taken, so a still-present search match means the click did nothing.
        page.wait_for_timeout(1200)
        if not verify_gone(page, player_id, name):
            raise RuntimeError(f"clicked {want} on {name} but he is still available — pick did not land")
        return f"{'queued' if queue else 'drafted'} {name}"


def verify_gone(page, player_id: int, name: str) -> bool:
    """True once `name` no longer shows as an undrafted search match."""
    box = page.locator("input[placeholder='Player Name']").first
    box.click()
    box.fill("")
    box.type(name, delay=15)
    page.wait_for_timeout(1000)
    return page.locator(
        f'button.player--search--match[data-player-search-playerid="{player_id}"]').count() == 0


if __name__ == "__main__":
    args = sys.argv[1:]
    with sync_playwright() as _p:
        _b = _p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        _page = _room(_b.contexts[0])
        if "--autopick-off" in args:
            print(autopick_off(_page))
            sys.exit(0)
    print(pick(int(args[0]), args[1], queue="--queue" in args))
