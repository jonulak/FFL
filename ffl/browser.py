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

from .config import DATA, DRAFT_ROOM_URL, ESPN_S2, LEAGUE_URL, ROOT, SWID

PROFILE = ROOT / ".chrome-profile"
CDP_PORT = 9222


def espn_cookies() -> list[dict]:
    if not (ESPN_S2 and SWID):
        return []
    base = {"domain": ".espn.com", "path": "/", "secure": True}
    return [{"name": "espn_s2", "value": ESPN_S2, **base, "httpOnly": True},
            {"name": "SWID", "value": SWID, **base}]


def launch(url: str):
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE), channel="chrome", headless=False,
            args=[f"--remote-debugging-port={CDP_PORT}", "--window-size=1600,1000"],
            viewport=None, ignore_default_args=["--enable-automation"],
        )
        if espn_cookies():
            ctx.add_cookies(espn_cookies())
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


def goto(url: str):
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        page = b.contexts[0].pages[0]
        page.goto(url, wait_until="domcontentloaded")
        print(page.url)


def screenshot(out: str):
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        ctx = b.contexts[0]
        page = next((pg for pg in ctx.pages if "espn.com" in pg.url), ctx.pages[0])
        page.screenshot(path=out, full_page=False)
        print(f"{page.url} -> {out}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "draft"
    if cmd == "goto":
        goto(sys.argv[2])
    elif cmd == "shot":
        screenshot(sys.argv[2] if len(sys.argv) > 2 else str(DATA / "shot.png"))
    else:
        launch(LEAGUE_URL if cmd == "league" else DRAFT_ROOM_URL)
