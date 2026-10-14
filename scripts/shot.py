"""Screenshots of season pages on a phone (390x844 @3x) or, with --wide, a 1280x800 desktop with a mouse.

    uv run python scripts/shot.py OUT_DIR PATH... [--base http://127.0.0.1:3302] [--wide] [--dark] [--full]
                                  [--click SELECTOR] [--hover SELECTOR]

One PNG per path, named from the path (/players?q=x -> players-q-x.png). --click / --hover act on every
page before the shot (open a sheet, hover a sort head); --full captures the whole scroll height.
"""
import argparse
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("paths", nargs="+")
ap.add_argument("--base", default="http://127.0.0.1:3302")
ap.add_argument("--wide", action="store_true")
ap.add_argument("--dark", action="store_true")
ap.add_argument("--full", action="store_true")
ap.add_argument("--click")
ap.add_argument("--hover")
a = ap.parse_args()
Path(a.out).mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1280, "height": 800} if a.wide else {"width": 390, "height": 844},
                        device_scale_factor=1 if a.wide else 3, is_mobile=not a.wide, has_touch=not a.wide,
                        color_scheme="dark" if a.dark else "light")
    for path in a.paths:
        page = ctx.new_page()
        page.goto(a.base + path, wait_until="networkidle", timeout=60_000)
        if a.click:
            page.click(a.click)
        if a.hover:
            page.hover(a.hover)
        page.wait_for_timeout(1200)
        name = re.sub(r"[^A-Za-z0-9]+", "-", path).strip("-") or "root"
        page.screenshot(path=f"{a.out}/{name}.png", full_page=a.full)
        print(f"{a.out}/{name}.png")
        page.close()
    b.close()
