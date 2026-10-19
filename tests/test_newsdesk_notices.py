from datetime import datetime, timedelta

from ffl import notify
from ffl.newsdesk import notices

AT = "2026-10-19T12:00:00-04:00"
ME, OTHER = 7, 3
OWNERS = {1: {"team_id": ME}, 2: {"team_id": OTHER}}  # 3 and up are free agents


def row(pid, effect, prob=0.9, reason="A reason.", flag=False, kind="injury", name=None):
    return {"player_id": pid, "effect": effect, "kind": kind, "prob": prob, "confidence": 0.5, "reason": reason,
            "flag": flag, "name": name or f"P{pid}"}


def story(*rows, sid=5, rev=1, headline="Something happened", status="live", note=None):
    return {"id": sid, "rev": rev, "headline": headline, "status": status, "status_note": note, "players": list(rows)}


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
    assert push(row(1, "down"), recent={(1, "injury")}) is None           # pushed for him today
    assert push(row(1, "down"), recent={(1, "trade")}) is not None
    n = push(row(1, "down"), row(2, "down"), row(3, "up"))
    assert n == {"key": "news:5:1", "kind": "news", "title": "Something happened", "body": "▼ P1: A reason. · ▲ P3: A reason.",
                 "url": "/news?story=5", "created_at": AT}


def test_body_order_down_first_then_prob():
    n = push(row(3, "up", prob=0.95), row(1, "down", prob=0.81), row(4, "up", prob=0.99))
    assert n["body"] == "▼ P1: A reason. · ▲ P4: A reason. · ▲ P3: A reason."
    assert notices.rows(story(row(3, "up"), row(1, "down")), OWNERS, ME, set()) == [(1, "injury"), (3, "injury")]


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


def test_recent_keeps_a_day_of_queued_and_sent():
    t = datetime.fromisoformat(AT)
    old = (t - timedelta(hours=25)).isoformat()
    sent = [{"key": "news:1:1", "created_at": AT, "outcome": "sent"},
            {"key": "news:2:1", "created_at": AT, "outcome": None},          # queued, not yet flushed
            {"key": "news:3:1", "created_at": AT, "outcome": "off"},
            {"key": "news:4:1", "created_at": old, "outcome": "sent"},
            {"key": "news:5:retracted", "created_at": AT, "outcome": "sent"}]
    stories = {1: story(row(1, "down"), row(6, "up", prob=0.79), row(7, "up", flag=True), sid=1),
               2: story(row(3, "up", kind="trade"), sid=2),
               3: story(row(8, "up"), sid=3), 4: story(row(9, "up"), sid=4)}
    assert notices.recent(sent, stories, t) == {(1, "injury"), (3, "trade")}


def test_material_update_detected_by_an_earlier_rev_pushed():
    assert notices.is_update(story(sid=5, rev=2), ["news:5:1"])
    assert not notices.is_update(story(sid=5, rev=2), [])
    assert not notices.is_update(story(sid=5, rev=1), ["news:5:1"])
    assert not notices.is_update(story(sid=5, rev=2), ["news:55:1", "news:5:retracted"])
