"""Launch a headed Chrome (persistent profile, ESPN cookies injected) on the draft room.

    uv run python -m ffl.browser            # open draft room, keep window alive
    uv run python -m ffl.browser league     # open league home instead
    uv run python -m ffl.browser shot out.png   # screenshot the running window (via CDP port)
    uv run python -m ffl.browser goto <url>     # navigate the running window

The window stays open until this process is killed. A second process can attach over CDP
(port 9222) to take screenshots while the user is drafting in the window.
"""
from __future__ import annotations

import sys
import time

from playwright.sync_api import sync_playwright

from . import config
from .config import CDP_PORT, DATA, ROOT

PROFILE = ROOT / ".chrome-profile"


def espn_cookies(user_id: int | None) -> list[dict]:
    """The acting user's own ESPN cookies for the window (ffl-soc.3: their row, never the environment); none when they
    have no working connection, and the window then asks ESPN's login."""
    from . import espn_auth
    c = espn_auth.own(user_id)
    if c is None:
        return []
    base = {"domain": ".espn.com", "path": "/", "secure": True}
    return [{"name": "espn_s2", "value": c.espn_s2, **base, "httpOnly": True},
            {"name": "SWID", "value": c.swid, **base}]


def launch(url: str, user_id: int | None = None):
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE), channel="chrome", headless=False,
            args=[f"--remote-debugging-port={CDP_PORT}", "--window-size=1600,1000"],
            viewport=None, ignore_default_args=["--enable-automation"],
        )
        if (jar := espn_cookies(user_id)):
            ctx.add_cookies(jar)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")
        print(f"Chrome open at {url}. CDP on :{CDP_PORT}. Ctrl-C to close.", flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            ctx.close()


def pick_page(ctx, match: str | None = None):
    """The tab we care about: an explicit match, else the draft room, else any ESPN tab."""
    pages = [pg for pg in ctx.pages if not pg.is_closed()]
    for want in ([match] if match else []) + ["/football/draft", "espn.com"]:
        page = next((pg for pg in pages if want in pg.url), None)
        if page:
            return page
    return pages[0] if pages else ctx.new_page()


def goto(url: str):
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        ctx = b.contexts[0]
        page = next((pg for pg in ctx.pages if "espn.com" in pg.url), None) or ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")
        print(page.url)


def screenshot(out: str, match: str | None = None):
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        ctx = b.contexts[0]
        page = pick_page(ctx, match)
        # no bring_to_front(): screenshotting must not steal the user's focus. CDP captures
        # a backgrounded tab fine.
        page.screenshot(path=out, full_page=False)
        print(f"{page.url} -> {out}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "draft"
    if cmd == "goto":
        goto(sys.argv[2])
    elif cmd == "shot":
        screenshot(sys.argv[2] if len(sys.argv) > 2 else str(DATA / "shot.png"),
                   sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        from . import leagues
        ref = leagues.acting()
        launch(config.league_url(ref) if cmd == "league" else config.draft_room_url(ref), ref.user_id)
