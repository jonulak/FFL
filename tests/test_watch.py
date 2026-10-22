from datetime import datetime, timedelta

from ffl import notify, watch
from ffl.adp import norm_name

AT = "2026-10-22T09:00:00-04:00"
NOW = datetime.fromisoformat(AT)
ACHANE = norm_name("De'Von Achane")


def p(pid=10, name="Kyle Pitts", pos="TE", **kw):
    return {"player_id": pid, "name": name, "pos": pos, "team": "ATL", "owner": None, "add": 0.0, **kw}


def chart(*rows):
    return [{"name": n, "pos": pos, "slot": pos, "order": o, "injury": inj} for n, pos, o, inj in rows]


def test_hurt_status_questionable_is_none():
    assert [watch.hurt_status(s) for s in ("doubtful", "OUT", "ir", "Injured Reserve", "injury_reserve")] == [
        "Doubtful", "Out", "IR", "IR", "IR"]
    assert [watch.hurt_status(s) for s in ("Questionable", "Active", None, "Day-To-Day")] == [None] * 4


def test_above_lower_order_same_pos_only():
    rows = chart(("Achane", "RB", 1, None), ("Wright", "RB", 2, None), ("Chase Brown", "RB", 3, None),
                 ("Some WR", "WR", 1, None))
    assert watch.above(p(name="Wright", pos="RB"), rows) == {norm_name("Achane")}
    assert watch.above(p(name="Nobody", pos="RB"), rows) == set()


def test_ahead_fires_for_starter_moved_down_after_ir():
    me = p(name="Wright", pos="RB")
    one = chart(("De'Von Achane", "RB", 1, None), ("Wright", "RB", 2, None))
    sigs, seen = watch.ahead_signals(me, one, {}, set(), AT)
    assert sigs == [] and seen == {ACHANE}
    two = chart(("Wright", "RB", 1, None), ("De'Von Achane", "RB", 3, "IR"))
    sigs, seen2 = watch.ahead_signals(me, two, {}, seen, AT)
    assert sigs == [{"key": f"ahead:10:{ACHANE}:IR", "player_id": 10, "kind": "ahead",
                     "text": "De'Von Achane IR", "at": AT}]
    assert seen2 == seen


def test_ahead_status_from_feed_and_name_fallbacks():
    me = p(name="Wright", pos="RB")
    rows = chart(("Wright", "RB", 1, None))
    sigs, _ = watch.ahead_signals(me, rows, {"gone back": "Out"}, {"gone back"}, AT, names={"gone back": "Gone Back"})
    assert sigs[0]["text"] == "Gone Back Out"
    sigs, _ = watch.ahead_signals(me, rows, {"gone back": "Out"}, {"gone back"}, AT)
    assert sigs[0]["text"] == "gone back Out"


def test_ahead_ignores_teammate_below():
    me = p(name="Pitts")
    rows = chart(("Kyle Pitts", "TE", 1, None), ("Wr Six", "WR", 6, "Out"), ("Te Two", "TE", 2, "Out"))
    assert watch.ahead_signals(me, rows, {}, set(), AT) == ([], set())


def form(*snaps, **cols):
    return {"rows": [{"week": i + 1, "snap_pct": s, **{k: v[i] for k, v in cols.items()}} for i, s in enumerate(snaps)]}


def test_usage_snap_jump():
    sig = watch.usage_signal(p(form=form(20, 24, 38)), AT)
    assert sig == {"key": "usage:10:3", "player_id": 10, "kind": "usage", "text": "snaps 22→38%", "at": AT}
    assert watch.usage_signal(p(form=form(20, 24, 30)), AT) is None
    assert watch.usage_signal(p(form=form(38)), AT) is None
    assert watch.usage_signal(p(), AT) is None


def test_usage_targets_and_red_zone():
    f = form(50, 50, 50, tgt=[2, 2, 7])
    assert watch.usage_signal(p(form=f), AT)["text"] == "targets 2→7"
    assert watch.usage_signal(p(pos="QB", form=f), AT) is None
    f = form(50, 50, 50, rz_tgt=[0, 0, 1], rz_att=[0, 0, 2])
    assert watch.usage_signal(p(pos="RB", form=f), AT)["text"] == "red-zone looks 0→3"
    f = form(None, None, None, tgt=[None, None, None])
    assert watch.usage_signal(p(form=f), AT) is None


def test_weakest_at_skips_ir():
    roster = [{"pos": "RB", "slot": "RB", "name": "A", "ros_adj": 50}, {"pos": "RB", "slot": "BE", "name": "B", "ros_adj": 30},
              {"pos": "RB", "slot": "IR", "name": "C", "ros_adj": 1}, {"pos": "WR", "slot": "WR", "name": "D", "ros_adj": 5}]
    assert watch.weakest_at(roster, "RB")["name"] == "B"
    assert watch.weakest_at(roster, "QB") is None


def test_bench_crossing_only():
    w = {"name": "Joe Bench", "pos": "RB"}
    me = p(pos="RB", delta_roster=0.4)
    assert watch.bench_signal(me, -1.0, w, 7, AT) == {
        "key": "bench:10:7", "player_id": 10, "kind": "bench", "text": "passes Joe Bench on your bench", "at": AT}
    assert watch.bench_signal(me, 0.0, None, 7, AT)["text"] == "passes your weakest RB"
    assert watch.bench_signal(p(delta_roster=0.9), 0.4, w, 7, AT) is None
    assert watch.bench_signal(me, None, w, 7, AT) is None


def test_available_only_on_drop():
    assert watch.available_signal(p(), 3, "2026-10-22", AT) == {
        "key": "available:10:2026-10-22", "player_id": 10, "kind": "available", "text": "available: dropped", "at": AT}
    assert watch.available_signal(p(), None, "2026-10-22", AT) is None
    assert watch.available_signal(p(owner=3), 3, "2026-10-22", AT) is None


def test_news_signal_prob_floor():
    story = {"id": 5, "rev": 2}
    row = {"player_id": 10, "effect": "up", "prob": 0.5, "reason": "Starting Sunday.", "flag": False}
    assert watch.news_signal(story, row, AT) == {
        "key": "news:10:5:2", "player_id": 10, "kind": "news", "text": "▲ Starting Sunday.", "at": AT}
    assert watch.news_signal(story, {**row, "prob": 0.49}, AT) is None
    assert watch.news_signal(story, {**row, "flag": True}, AT) is None
    assert watch.news_signal(story, {**row, "effect": "down"}, AT) is None
    assert watch.news_signal(story, {**row, "reason": " "}, AT) is None


def test_notice_body_capped_and_keyed_by_day():
    sigs = [{"text": "x" * 100}, {"text": "y" * 100}]
    n = watch.notice_for(p(), sigs, "2026-10-22", AT)
    assert n["key"] == "watch:10:2026-10-22" and n["kind"] == "watch" and n["title"] == "Kyle Pitts"
    assert n["url"] == "/watch" and n["created_at"] == AT and len(n["body"]) <= notify.BODY_MAX
    assert watch.notice_for(p(), [{"text": "a"}, {"text": "b"}], "d", AT)["body"] == "a · b"


def sig(pid, kind, days, text="t"):
    return {"key": f"{kind}:{pid}:{days}", "player_id": pid, "kind": kind, "text": text,
            "at": (NOW - timedelta(days=days)).isoformat()}


def test_page_groups():
    rows = [p(1, "Owned", owner=4, add=9), p(2, "Fresh", add=1), p(3, "Stale", add=5), p(4, "Idle", add=7),
            p(5, "Dropped", add=0)]
    sigs = [sig(1, "usage", 0), sig(2, "usage", 2, "snaps"), sig(2, "usage", 3.5), sig(3, "news", 4),
            sig(5, "available", 1)]
    out = watch.page(rows, sigs, NOW)
    assert [r["player_id"] for r in out["elsewhere"]] == [1]
    assert [r["player_id"] for r in out["moving"]] == [5, 2]
    assert [r["player_id"] for r in out["quiet"]] == [4, 3]
    fresh = out["moving"][1]
    assert [c["kind"] for c in fresh["chips"]] == list(watch.KINDS)
    usage = fresh["chips"][1]
    assert usage["lit"] and usage["text"] == "snaps"
    stale = out["quiet"][1]["chips"][2]
    assert not stale["lit"] and stale["text"] == "t"
    assert out["quiet"][0]["chips"][0] == {"kind": "ahead", "text": None, "at": None, "lit": False}
