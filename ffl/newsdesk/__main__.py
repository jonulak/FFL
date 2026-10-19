"""uv run python -m ffl.newsdesk run|seed|status

run: one pipeline run here, printing each stage (its pushes are queued; the next notify tick sends them).
seed: register the sources. status: each source's health and the last run's stages.
"""
from __future__ import annotations

import json
import sys

from .. import db
from . import pipeline, seed


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "run":
        print(json.dumps(pipeline.run(), indent=2, default=str))
    elif cmd == "seed":
        print(f"{seed.ensure()} sources registered")
    elif cmd == "status":
        for s in db.news_sources(active_only=False):
            state = "off" if not s["active"] else f"failing since {s['failing_since']}: {s['error']}" if s["error"] else f"ok {s['ok_at']}"
            print(f"{s['id']:<40} {state}")
        print(json.dumps(db.get_state("news_last_run"), indent=2, default=str))
    else:
        print("usage: python -m ffl.newsdesk run|seed|status")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
