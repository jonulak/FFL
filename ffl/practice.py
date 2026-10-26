"""Start a League Specific Practice Draft from ESPN's Mock Draft Lobby.

    uv run python -m ffl.practice 4        # practice draft from slot 4
    uv run python -m ffl.practice --check  # is the Chrome window logged in?

Drives the Chrome window `ffl.browser` already has open (CDP). Practice drafts run against
ESPN Auto teams with our league's own settings, and they are the only way to rehearse before
the real draft: the league draft room URL redirects to the fantasy home page whenever the
real draft is not live.

Things learned the hard way, encoded here:
  - The lobby is `/football/mockdraftlobby`; the card's button is `button.Button--alt`
    labelled "Practice Draft". The league draft room is NOT an entry point.
  - The slot picker is a radio group, not buttons: `input[type=radio][value='4']`, and the
    label intercepts a real mouse click, so dispatch the click on the element itself.
  - The room opens in a NEW TAB. It keeps our real team id (7) but seats us at the chosen
    slot in an otherwise shuffled order, so the league's stored settings' pick order is wrong for the
    mock — let the watcher learn it (it did, across five refinements by pick 10).
  - espn_s2/SWID authenticate the API but not the web UI. If the window is logged out, log in
    at https://www.espn.com/login by hand: the form is in an iframe from
    cdn.registerdisney.go.com. The session then persists in `.chrome-profile/`.
"""
from __future__ import annotations

import argparse
import sys

from playwright.sync_api import sync_playwright

from .config import CDP_PORT

LOBBY = "https://fantasy.espn.com/football/mockdraftlobby"
DRAFT_URL_MATCH = "/football/draft"


def _connect(p):
    return p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}").contexts[0]


def _espn_page(ctx):
    pages = [pg for pg in ctx.pages if not pg.is_closed() and "espn.com" in pg.url]
    if not pages:
        raise SystemExit("No ESPN tab open. Run `uv run python -m ffl.browser` first.")
    return pages[0]


def logged_in(page) -> bool:
    """The fantasy nav shows "My Teams" when signed in and "Sign Up" alone when not."""
    return bool(page.locator("text=My Teams").count())


def check() -> bool:
    with sync_playwright() as p:
        page = _espn_page(_connect(p))
        if LOBBY not in page.url:
            page.goto(LOBBY, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
        ok = logged_in(page)
        print("logged in" if ok else
              "NOT logged in — sign in by hand at https://www.espn.com/login in that window")
        return ok


def start(slot: int) -> str:
    """Open the lobby, choose the slot, start the draft; returns the room's URL."""
    with sync_playwright() as p:
        ctx = _connect(p)
        page = _espn_page(ctx)
        page.goto(LOBBY, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        if not logged_in(page):
            raise SystemExit("Chrome window is not logged in to ESPN — see `--check`.")
        btn = page.locator("button.Button--alt", has_text="Practice Draft")
        if not btn.count():
            raise SystemExit("No 'Practice Draft' button in the lobby (is the league set up?)")
        btn.first.click()
        page.wait_for_timeout(3000)
        radio = page.locator(f"input[type=radio][value='{slot}']")
        if not radio.count():
            raise SystemExit(f"No draft position {slot} offered")
        # the FormControlLabel overlays the input, so a real mouse click never lands on it
        radio.first.evaluate("el => el.click()")
        page.wait_for_timeout(500)
        page.get_by_role("button", name="Start Practice Draft").first.click()
        page.wait_for_timeout(8000)
        room = next((pg.url for pg in ctx.pages
                     if not pg.is_closed() and DRAFT_URL_MATCH in pg.url), None)
        if not room:
            raise SystemExit("Draft room tab did not open")
        print(f"practice draft room: {room}")
        print("Register the mock league, then start the watcher against it:\n"
              "  export FFL_DB_URL=postgresql+psycopg:///ffl_mock FFL_DATA_DIR=data-mock\n"
              "  uv run python -m ffl.leagues add espn <mock id from that url> --home\n"
              "  uv run python -m ffl.draft_watch")
        return room


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("slot", nargs="?", type=int, help="draft position, 1..10")
    ap.add_argument("--check", action="store_true", help="report whether Chrome is logged in")
    a = ap.parse_args()
    if a.check:
        sys.exit(0 if check() else 1)
    if not a.slot:
        ap.error("give a draft position (1..10) or --check")
    start(a.slot)
