from datetime import datetime, timedelta

from ffl import notify
from ffl.newsdesk import notices

AT = "2026-10-19T12:00:00-04:00"
ME, OTHER = 7, 3
OWNERS = {1: {"team_id": ME}, 2: {"team_id": OTHER}}  # 3 and up are free agents


def row(pid, effect, prob=0.9, reason="A reason.", flag=False, kind="injury", name=None, pos="WR"):
    return {"player_id": pid, "effect": effect, "kind": kind, "prob": prob, "confidence": 0.5, "reason": reason,
            "flag": flag, "name": name or f"P{pid}", "pos": pos}


FRESH = "2026-10-19T15:30:00+00:00"  # half an hour before AT


def story(*rows, sid=5, rev=1, headline="Something happened", status="live", note=None, published=FRESH, status_at=FRESH):
    return {"id": sid, "rev": rev, "headline": headline, "status": status, "status_note": note, "status_at": status_at,
            "players": list(rows), "items": [{"published_at": "2026-10-18T09:00:00+00:00"}, {"published_at": published}]}


def push(*rows, **kw):
    return notices.for_story(story(*rows), OWNERS, ME, set(), AT, **kw)


def test_for_story_gates():
    assert push(row(1, "down"))["body"] == "▼ P1: A reason."              # mine ▼ in
    assert push(row(1, "up")) is None                                     # mine ▲ out
    assert push(row(3, "up"))["body"] == "▲ P3: A reason."                # free agent ▲ in
    assert push(row(2, "down")) is None                                   # another roster's ▼ out
    assert push(row(3, "down")) is None                                   # a free agent's ▼ out
    assert push(row(1, "down", prob=0.79)) is None
    assert push(row(1, "down", prob=0.8)) is not None
    assert push(row(1, "down", flag=True)) is None
    assert push(row(1, "down", reason=None)) is None
    assert push(row(1, "down", reason="  ")) is None
    assert push(row(1, "none")) is None
    assert notices.for_story(story(row(1, "down")), OWNERS, ME, {1}, AT) is None   # muted
    assert notices.for_story(story(row(1, "down"), headline=""), OWNERS, ME, set(), AT) is None
    assert push(row(1, "down"), recent={1}) is None           # pushed for him today
    assert push(row(1, "down"), recent={(1, "trade")}) is not None
    n = push(row(1, "down"), row(2, "down"), row(3, "up"))
    assert n == {"key": "news:5:1", "kind": "news", "title": "Something happened", "body": "▼ P1: A reason. · ▲ P3: A reason.",
                 "url": "/news?story=5", "created_at": AT}


def test_kicker_and_defense_push_only_as_my_injury_or_release():
    assert push(row(3, "up", pos="K")) is None                            # free-agent kicker out
    assert push(row(3, "up", pos="D/ST")) is None                         # free-agent defense out
    assert push(row(1, "down", pos="K")) is not None                      # my kicker's injury in
    assert push(row(1, "down", pos="K", kind="role")) is None             # my kicker's other news out
    assert push(row(1, "down", pos="K", kind="role", reason="The Bears released him.")) is not None
    assert push(row(1, "down", pos="K", kind="other", reason="Waived after two misses.")) is not None
    assert push(row(1, "down", pos="K", kind="role", reason="Cut on Tuesday.")) is not None
    assert push(row(3, "down", pos="K", kind="role", reason="Released.")) is None   # not mine
    assert push(row(1, "down", pos=None)) is not None                     # no position: the usual rules


def test_body_order_down_first_then_prob():
    n = push(row(3, "up", prob=0.95), row(1, "down", prob=0.81), row(4, "up", prob=0.99))
    assert n["body"] == "▼ P1: A reason. · ▲ P4: A reason. · ▲ P3: A reason."
    assert notices.rows(story(row(3, "up"), row(1, "down")), OWNERS, ME, set()) == [1, 3]


def test_body_whole_lines_with_more_suffix():
    reason = "He was carted off in the second quarter and the club ruled him out for the rest of the game."
    n = push(*[row(pid, "up", prob=0.9 - pid / 100, reason=reason) for pid in range(3, 8)])
    body = n["body"]
    assert len(body) <= notify.BODY_MAX
    assert body.endswith(" +4 more")
    lines = body.removesuffix(" +4 more").split(" · ")
    assert lines == [f"▲ P3: {reason}"]
    assert notify._cap(body) == body


def test_one_line_too_long_is_capped_whole():
    n = push(row(1, "down", reason="x" * 300))
    assert len(n["body"]) <= notify.BODY_MAX and n["body"].startswith("▼ P1: x")


def test_key_per_rev():
    assert notices.for_story(story(row(1, "down"), sid=9, rev=2), OWNERS, ME, set(), AT)["key"] == "news:9:2"


def test_status_push_only_if_original_pushed():
    s = story(row(1, "down"), sid=9, rev=3, headline="Report withdrawn: Hurts out", status="retracted", note="PFT")
    n = notices.for_status(s, ["news:9:1"], AT)
    assert n == {"key": "news:9:retracted", "kind": "news", "title": "Retracted: Report withdrawn: Hurts out",
                 "body": "PFT", "url": "/news?story=9", "created_at": AT}
    assert notices.for_status(s, [], AT) is None
    assert notices.for_status({**s, "headline": " "}, ["news:9:1"], AT) is None
    assert notices.for_status(s, ["news:19:1", "news:91:1"], AT) is None
    c = story(row(1, "down"), sid=9, rev=2, headline="Hurts out", status="corrected", note="ESPN")
    assert notices.for_status(c, ["news:9:1"], AT)["title"] == "Corrected: Hurts out"
    assert notices.for_status(story(row(1, "down"), sid=9), ["news:9:1"], AT) is None   # live: no status push


def test_recent_keeps_the_window_of_queued_and_sent():
    t = datetime.fromisoformat(AT)
    old = (t - timedelta(hours=49)).isoformat()
    sent = [{"key": "news:1:1", "created_at": AT, "outcome": "sent"},
            {"key": "news:2:1", "created_at": AT, "outcome": None},          # queued, not yet flushed
            {"key": "news:3:1", "created_at": AT, "outcome": "off"},
            {"key": "news:4:1", "created_at": old, "outcome": "sent"},
            {"key": "news:5:retracted", "created_at": AT, "outcome": "sent"}]
    stories = {1: story(row(1, "down"), row(6, "up", prob=0.79), row(7, "up", flag=True), sid=1),
               2: story(row(3, "up", kind="trade"), sid=2),
               3: story(row(8, "up"), sid=3), 4: story(row(9, "up"), sid=4)}
    assert notices.recent(sent, stories, t) == {1, 3}


def test_urgent_reads_the_newest_source():
    s = {"players": [{"player_id": 1, "effect": "down"}, {"player_id": 2, "effect": "up"}],
         "items": [{"id": 1, "published_at": "2026-10-19T01:00:00+00:00", "jev": {}},
                   {"id": 2, "published_at": "2026-10-19T02:00:00+00:00",
                    "jev": {"status:1": {"status": "ir"}, "fresh:1": {"noul": 1.0}, "status:2": {"status": "active"}, "fresh:2": {"noul": 1.0}}}]}
    assert notices.urgent(s) == {1}
    s["items"][1]["jev"]["fresh:1"]["noul"] = 0.0
    assert notices.urgent(s) == set()
    assert notices.urgent({"players": [], "items": []}) == set()


def test_a_clearing_counts_only_on_the_newest_source():
    s = {"players": [{"player_id": 1, "effect": "up"}],
         "items": [{"id": 1, "published_at": "2026-10-19T01:00:00+00:00", "jev": {"ready:1": {"type": "ready"}}},
                   {"id": 2, "published_at": "2026-10-19T02:00:00+00:00", "jev": {}}]}
    assert notices.ready(s) == set() and notices.urgent(s) == set()
    s["items"][1]["jev"] = {"ready:1": {"type": "ready"}}
    assert notices.ready(s) == {1} and notices.urgent(s) == {1}


def test_ready_pushes_only_for_my_own_player():
    assert push({**row(1, "up"), "ready": True}) is not None  # mine
    assert push(row(1, "up")) is None


def test_a_free_agent_matters_by_ownership_or_the_chart():
    assert notices.matters({}) and notices.matters({"owned": 5.0}) and notices.matters({"owned": 0.1, "starter": True})
    assert not notices.matters({"owned": 4.9})


def test_old_news_does_not_push():
    """The first run collects three days of items, and a catch-up after a Haiku-limit stretch writes old stories:
    only news whose newest source is at most NEWS_PUSH_MAX_AGE old pushes; the feed still shows the rest."""
    t = datetime.fromisoformat(AT)
    at_edge = (t - notices.NEWS_PUSH_MAX_AGE).isoformat()
    stale = (t - notices.NEWS_PUSH_MAX_AGE - timedelta(minutes=1)).isoformat()
    assert notices.NEWS_PUSH_MAX_AGE == timedelta(hours=6)
    assert notices.for_story(story(row(1, "down"), published=at_edge), OWNERS, ME, set(), AT) is not None
    assert notices.for_story(story(row(1, "down"), published=stale), OWNERS, ME, set(), AT) is None
    assert notices.for_story({**story(row(1, "down")), "items": []}, OWNERS, ME, set(), AT) is None
    s = story(row(1, "down"), sid=9, rev=2, status="retracted", published=stale)
    assert notices.for_status({**s, "status_at": at_edge}, ["news:9:1"], AT) is not None  # the retraction's own time
    assert notices.for_status({**s, "status_at": stale}, ["news:9:1"], AT) is None


def test_status_note_names_the_source():
    s = story(row(1, "down"), sid=9, rev=2, headline="Hurts out", status="corrected", note="espn:LAR")
    sources = {"espn:LAR": {"id": "espn:LAR", "name": "ESPN Rams"}}
    assert notices.for_status(s, ["news:9:1"], AT, sources)["body"] == "ESPN Rams"
    assert notices.for_status(s, ["news:9:1"], AT)["body"] == "espn:LAR"  # an unknown source: its id
