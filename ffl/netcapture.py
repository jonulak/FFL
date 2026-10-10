"""Record what ESPN's own web app sends, from the headed Chrome (`ffl.browser`), over CDP.

    uv run python -m ffl.netcapture out.jsonl      # log every fantasy API call; Ctrl-C to stop

One JSON line per ESPN fantasy API response: method, url, request body, status, response body. Cookies and headers
are never written. Every call is logged (reads too), so a growing file proves the listener is live: check it has
lines from an ordinary page load before making the move you want captured. A one-shot capture of a real roster
move is lost if the listener is quietly dead (ffl-ut0.7, 2026-10-09).

The loop waits with `page.wait_for_timeout`, never `time.sleep`: the sync API delivers events only while it is
inside one of its own calls, so a sleeping loop attaches fine and records nothing.
"""
from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright

from .config import CDP_PORT

API_HOSTS = ("lm-api-reads.fantasy.espn.com", "lm-api-writes.fantasy.espn.com")


def wanted(url: str) -> bool:
    return any(h in url for h in API_HOSTS)


def record(response) -> dict:
    req = response.request
    try:
        body = response.text()
    except Exception as e:  # redirects and aborted calls have no body
        body = f"<no body: {e}>"
    return {"t": time.time(), "method": req.method, "url": response.url, "req": req.post_data,
            "status": response.status, "resp": body}


def main(out_path: str) -> None:
    out = open(out_path, "a")
    seen = {"reads": 0, "writes": 0}

    def on_response(response):
        if not wanted(response.url):
            return
        out.write(json.dumps(record(response)) + "\n")
        out.flush()
        kind = "writes" if response.request.method != "GET" else "reads"
        seen[kind] += 1
        tag = "WRITE " if kind == "writes" else ""
        print(f"{tag}{response.request.method} {response.status} {response.url[:120]}", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        ctx = browser.contexts[0]
        for page in ctx.pages:
            page.on("response", on_response)
        ctx.on("page", lambda page: page.on("response", on_response))
        print(f"listening on :{CDP_PORT} -> {out_path} (load any ESPN page to see a read logged)", flush=True)
        try:
            while True:
                pages = [pg for pg in ctx.pages if not pg.is_closed()]
                if not pages:
                    break
                pages[0].wait_for_timeout(1000)  # pumps events; time.sleep would not
        except KeyboardInterrupt:
            pass
        print(f"stopped: {seen['reads']} reads, {seen['writes']} writes", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
