"""Source health: a notice once a news source has failed for a day, like notify.source_notices for projections."""
from __future__ import annotations

from datetime import datetime, timedelta


def notices(sources: list[dict], t: datetime, at: str, hours: int = 24) -> list[dict]:
    """One notice per failing spell of a `news_sources` row (keyed by when it began) once it passes `hours`."""
    out = []
    for s in sorted(sources, key=lambda s: s["id"]):
        since = s.get("failing_since")
        if not since or t - datetime.fromisoformat(since) < timedelta(hours=hours):
            continue
        out.append({"key": f"news-source:{s['id']}:{since}", "kind": "news-source", "title": f"{s['name']} news failing",
                    "body": f"Since {since[:16].replace('T', ' ')}: {s.get('error')}. The other sources still run.",
                    "url": "/news", "created_at": at})
    return out
