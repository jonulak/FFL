from datetime import datetime, timezone

import pytest

from ffl.newsdesk import rules, write
from ffl.newsdesk.players import Index

NOW = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)
AT = "2026-10-19T12:00:00+00:00"
WILLIAMS = {"player_id": 1, "name": "Kyren Williams", "pos": "RB", "team": "LAR"}
NACUA = {"player_id": 2, "name": "Puka Nacua", "pos": "WR", "team": "LAR"}
TAYLOR = {"player_id": 3, "name": "Jonathan Taylor", "pos": "RB", "team": "IND"}


def item(title, text="", source="site:LAR", at=AT):
    return {"title": title, "text": text, "source_id": source, "published_at": at}


def said(title, text="", player=WILLIAMS, players=None, last=None, **kw):
    """(effect, fresh, prob, kind, about, subject) of `player` in this item."""
    cands = []
    if last:
        key = f"status:{player['player_id']}"
        cands = [{"id": 9, "items": [{"published_at": "2026-10-19T01:00:00+00:00", "jev": {key: {"status": last}}}]}]
    got = rules.answer(item(title, text, **kw), players or [player], cands)
    pid = player["player_id"]
    return (got[f"effect:{pid}"]["choice"], got[f"fresh:{pid}"]["noul"], got[f"effect:{pid}"]["confidence"],
            got[f"kind:{pid}"]["choice"], got[f"about:{pid}"]["noul"], got[f"subject:{pid}"]["noul"])


@pytest.mark.parametrize("title,effect,kind", [
    ("Williams (knee) has been ruled out for Sunday's game", "down", "injury"),
    ("Rams rule out Kyren Williams (ankle) vs. Bengals", "down", "injury"),
    ("Rams placed Kyren Williams on injured reserve", "down", "injury"),
    ("Williams (hip) didn't practice Thursday.", "down", "injury"),
    ("Kyren Williams is doubtful for Sunday", "down", "injury"),
    ("Rams release Kyren Williams", "down", "role"),
    ("Kyren Williams left the game with a hamstring injury", "down", "injury"),
    ("Rams activate Kyren Williams off injured reserve", "up", "return"),
    ("Williams (knee) returned to practice Thursday", "up", "return"),
])
def test_a_status_gives_its_arrow(title, effect, kind):
    got = said(title)
    assert got[0] == effect and got[1] == 1.0 and got[3] == kind
    assert got[2] >= 0.8  # pushes


@pytest.mark.parametrize("title", ["Kyren Williams limited in practice", "Kyren Williams will start Sunday", "Rams sign Kyren Williams",
                                   "Kyren Williams was benched"])
def test_a_weak_rule_stays_under_the_push_cutoff(title):
    assert said(title)[2] < 0.8


@pytest.mark.parametrize("last,effect", [("dnp", "up"), ("active", "down"), (None, "down")])
def test_limited_reads_against_his_last_status(last, effect):
    assert said("Kyren Williams limited at Thursday's practice", last=last)[0] == effect


def test_the_same_status_again_is_not_news():
    got = said("Williams did not practice Thursday", last="dnp")
    assert got[:2] == ("none", 0.0)


def test_out_after_ir_is_no_recovery_and_full_after_active_no_setback():
    assert said("Kyren Williams ruled out for Sunday", last="ir")[:2] == ("none", 0.0)
    assert said("Kyren Williams was a full participant", last="active")[:2] == ("none", 0.0)


@pytest.mark.parametrize("title", ["Kyren Williams could miss Sunday", "If Kyren Williams will miss Sunday, Blake Corum starts",
                                   "Kyren Williams after missing practice last week", "Kyren Williams avoided a torn ACL",
                                   "Kyren Williams remains on injured reserve"])
def test_what_did_not_happen_gives_no_arrow(title):
    assert said(title)[0] == "none"


def test_from_ir_is_a_return_not_ir():
    assert said("Rams activated Kyren Williams from injured reserve")[0] == "up"


def test_status_stays_with_the_player_it_is_about():
    title = "Kyren Williams ruled out, Puka Nacua full participant"
    both = [WILLIAMS, NACUA]
    assert said(title, players=both)[0] == "down"
    assert said(title, player=NACUA, players=both)[0] != "down"


@pytest.mark.parametrize("name,title", [
    ("Jonathan Taylor", "Colts place CB Cam Taylor-Britt on injured reserve"),
    ("Jonathan Taylor", "Bengals place Akayleb Taylor on injured reserve"),
    ("Tyrod Taylor", "The Panthers placed starting right tackle Taylor Moton on IR"),
    ("Kyren Williams", "Rams head coach Williams said the line is fine"),
])
def test_another_man_with_the_name_is_not_him(name, title):
    p = {"player_id": 3, "name": name, "pos": "RB", "team": "IND"}
    assert said(title, player=p)[4] == 0.0 or said(title, player=p)[0] == "none"


@pytest.mark.parametrize("title", [
    "Williams (hamstring/ankle) was listed as a limited participant in Tuesday's practice.",
    "The Rams listed Williams (hamstring) as a limited participant on Wednesday's estimated practice report",
    "Williams (hamstring) didn't take part in Wednesday's practice and instead was seen working on the side",
    "Williams went down as a non-participant on Tuesday's injury report",
    "No sign of starting RB Kyren Williams (foot) at the media-access portion of Wednesday's practice",
])
def test_practice_report_wording_moves_him_down(title):
    assert said(title)[0] == "down"


@pytest.mark.parametrize("title,status", [
    ("Seahawks RB Kyren Williams is going to be out Sunday vs. the Cardinals due to a quad injury", "out"),
    ("Kyren Williams out Week 9 with quad injury, Blake Corum in line to start", "out"),
    ("Williams (ankle) out Sunday against the Cardinals", "out"),
    ("Sources: Rams RB Kyren Williams (turf toe) to miss rest of 2026 season", "ir"),
    ("Kyren Williams Named Rams Starting Running Back", "starter"),
    ("Williams will be removed from the starting role", "benched"),
])
def test_shorthand_for_a_status_reads_as_that_status(title, status):
    assert (rules.answer(item(title), [WILLIAMS], []).get(f"status:{WILLIAMS['player_id']}") or {}).get("status") == status


@pytest.mark.parametrize("title", [
    "Kyren Williams: I don't want to be out there if I am not the best option",
    "Kyren Williams worked out Sunday with the rookies",
    "The Rams haven't officially named Kyren Williams this week's starter",
])
def test_words_near_a_status_are_not_that_status(title):
    assert said(title)[0] == "none"


def test_a_title_of_several_sentences_keeps_each_mans_status_apart():
    title = "Sean McVay said Puka Nacua hurt his quad and is out this week. Says he's week to week. Kyren Williams will start this week."
    assert said(title)[0] == "up"


def test_a_clause_after_as_is_still_cut_when_it_is_someone_else():
    assert said("Kyren Williams ran hard as Puka Nacua left the game with a hamstring injury", players=[WILLIAMS, NACUA])[0] == "none"


@pytest.mark.parametrize("title", [
    "Took 28 minutes, but we have a sign of life from the Rams offense. Kyren Williams into the end zone",
    "3 Stats to Know: Another Sign of Kyren Williams' growth",
])
def test_a_sign_of_something_is_no_signing(title):
    assert said(title)[0] == "none"


def test_a_bare_surname_after_another_man_of_that_name_is_not_his():
    text = "Doug Williams, who was MVP of Super Bowl 22, is among five candidates.\n\nWilliams, the first Black quarterback to start in a Super Bowl, is joined by others."
    assert said("Hall of Fame names Doug Williams, Robert Kraft among 5 candidates", text)[0] == "none"
    assert said("Rams update", "Doug Williams praised the back.\n\nKyren Williams will start Sunday.")[0] == "up"


def test_a_bare_surname_two_players_share_is_the_owner_of_the_note():
    brian = {"player_id": 4, "name": "Brian Robinson Jr.", "pos": "RB", "team": "ATL"}
    bijan = {"player_id": 5, "name": "Bijan Robinson", "pos": "RB", "team": "ATL"}
    note = item("Robinson (hamstring) won't practice Wednesday", "Bijan Robinson should see more work.", source="rotowire") | {"url": "https://x/id/4"}
    got = rules.answer(note, [brian, bijan], [])
    assert got["effect:4"]["choice"] == "down" and got["effect:5"]["choice"] == "none"
    # no owner to go by: neither, unless the title spells him out
    assert said("Robinson (hamstring) won't practice Wednesday", player=bijan, players=[brian, bijan])[0] == "none"
    assert said("Bijan Robinson (hamstring) won't practice Wednesday", player=bijan, players=[brian, bijan])[0] == "down"


def test_a_position_that_is_not_his_is_not_him():
    assert said("Rams WR Williams caught two passes", player=TAYLOR | {"name": "Kyren Williams"})[4] == 0.0


def test_a_status_deep_in_an_article_gives_no_arrow():
    body = " ".join(["The Rams are preparing for Sunday."] * 30) + " Kyren Williams was ruled out."
    assert said("Rams prepare for the Bengals", body)[0] == "none"


@pytest.mark.parametrize("title", ["5 takeaways: Kyren Williams ruled out", "Williams rushed for 80 yards after Kyren Williams left the game"])
def test_a_roundup_or_a_stat_line_gives_no_arrow(title):
    assert said(title)[0] == "none"


def test_a_roster_move_list_counts_every_line():
    assert said("Rams announce roster moves", "The Rams placed Kyren Williams on injured reserve.")[0] == "down"


def test_a_passing_mention_is_no_subject():
    body = " ".join(["The Rams are preparing for Sunday."] * 30) + " Kyren Williams ran hard."
    assert said("Rams prepare for the Bengals", body)[5] == 0.0
    assert said("Kyren Williams ran hard")[5] == 1.0


def test_media_is_never_an_arrow():
    assert said("Kyren Williams ruled out", source="yt:LAR")[:2] == ("none", 0.0)


def test_a_media_excerpt_keeps_its_own_player_whatever_it_calls_him():
    excerpt = item("Sean McVay press conference", "Kyren was sharp today, he had a great week.", source="yt:LAR")
    excerpt["ext_id"] = "vid#1"
    got = rules.answer(excerpt, [WILLIAMS, NACUA], [])
    assert (got["about:1"]["noul"], got["subject:1"]["noul"]) == (True, True) and got["effect:1"]["choice"] == "none"
    assert got["about:2"]["noul"] == 0.0


@pytest.mark.parametrize("title", ["Kyren Williams again ruled out for Week 7", "Kyren Williams will miss a second straight game"])
def test_a_repeat_of_old_news_is_not_a_new_step(title):
    assert said(title)[0] == "none"


def test_a_suffix_after_a_bare_surname_is_still_him():
    assert said("Robinson Jr. ruled out for Sunday", player={"player_id": 8, "name": "Brian Robinson Jr.", "pos": "RB", "team": "ATL"})[0] == "down"


@pytest.mark.parametrize("title,text,effect", [
    ("Rams star Kyren Williams returns to Rams practice", "Williams did not practice last week.", "up"),
    ("Kyren Williams, Puka Nacua return to practice", "", "up"),
    ("Kyren Williams active, Puka Nacua inactive for Rams-Raiders", "", "up"),
    ("Kyren Williams Available, Puka Nacua ruled out", "", "none"),
    ("Kyren Williams, Puka Nacua questionable, Blake Corum ruled out", "", "down"),  # limited, not out
    ("Coach said Kyren Williams isn't expected to practice Wednesday", "", "down"),
    ("Neither Kyren Williams nor Puka Nacua will practice today", "", "down"),
    ("Kyren Williams is out of the medical tent", "", "none"),
    ("A deep shot to Kyren Williams on his first play back from the hospital", "", "none"),
    ("Monken said he's optimistic that Williams will be activated from injured reserve", "", "none"),
    ("Kyren Williams is expected to practice Wednesday", "He suffered a concussion.", "up"),
    ("Kyren Williams is expected to be held out and miss the rest of the season", "", "down"),
])
def test_phrasing_that_once_read_the_wrong_way(title, text, effect):
    assert said(title, text)[0] == effect


def test_a_list_gives_him_his_own_status_not_the_next_mans():
    got = rules.answer(item("Kyren Williams, Puka Nacua questionable, Blake Corum ruled out"), [WILLIAMS], [])
    assert got["status:1"]["status"] == "limited" and got["effect:1"]["confidence"] < 0.8


@pytest.mark.parametrize("title,text,last,cleared", [
    ("Williams (ankle) is active for Sunday's game", "", None, True),
    ("Kyren Williams will play Sunday", "He was listed as questionable.", None, True),
    ("Kyren Williams will play Sunday", "", "limited", True),
    ("Kyren Williams will play Sunday", "", None, False),  # no tag on record: nothing hung on it
    ("Rams activate Kyren Williams off injured reserve", "He was questionable.", None, False),  # a roster move, no game call
    ("Kyren Williams returned to practice", "He was questionable.", None, False),
    ("Kyren Williams returns to practice", "Williams is expected to play Sunday. He was questionable.", None, False),  # the body's word
])
def test_a_tagged_player_cleared_for_the_game(title, text, last, cleared):
    cands = [{"id": 9, "items": [{"published_at": "2026-10-19T01:00:00+00:00", "jev": {"status:1": {"status": last}}}]}] if last else []
    assert ("ready:1" in rules.answer(item(title, text), [WILLIAMS], cands)) is cleared


def test_retract_and_story_pick():
    prior = {"id": 7, "items": [{"title": "Kyren Williams hamstring injury sidelines Rams back",
                                 "text": "", "player_ids": [1], "published_at": "2026-10-19T01:00:00+00:00"}]}
    other = {"id": 8, "items": [{"title": "Kyren Williams signs extension with Los Angeles",
                                 "text": "", "player_ids": [1], "published_at": "2026-10-19T02:00:00+00:00"}]}
    got = rules.answer(item("Kyren Williams hamstring injury sidelines Rams star back again"), [WILLIAMS], [other, prior])
    assert got["story"]["choice"] == "s7" and got["retract"]["noul"] == 0.0
    assert rules.answer(item("Correction: the Kyren Williams report was wrong"), [WILLIAMS], [other, prior])["retract"]["noul"] == 1.0
    assert rules.answer(item("Kyren Williams hosts a youth football camp in Ohio"), [WILLIAMS], [other, prior])["story"]["choice"] == "new"


CHART = {"LAR": [{"name": "Kyren Williams", "pos": "RB", "slot": "RB", "order": 1, "injury": None},
                 {"name": "Blake Corum", "pos": "RB", "slot": "RB", "order": 2, "injury": None},
                 {"name": "Ronnie Rivers", "pos": "RB", "slot": "RB", "order": 3, "injury": None},
                 {"name": "Puka Nacua", "pos": "WR", "slot": "LWR", "order": 1, "injury": None},
                 {"name": "Jordan Whittington", "pos": "WR", "slot": "SWR", "order": 1, "injury": None},
                 {"name": "Xavier Smith", "pos": "WR", "slot": "LWR", "order": 2, "injury": None}]}
BY_ID = {1: WILLIAMS, 2: NACUA, 4: {"name": "Blake Corum", "pos": "RB", "team": "LAR"},
         5: {"name": "Ronnie Rivers", "pos": "RB", "team": "LAR"}, 6: {"name": "Xavier Smith", "pos": "WR", "team": "LAR"},
         7: {"name": "Jordan Whittington", "pos": "WR", "team": "LAR"}}
INDEX = Index(by_id={k: {x: v[x] for x in ("name", "pos", "team")} for k, v in BY_ID.items()},
              owned={1: 90.0, 2: 99.0, 4: 40.0, 5: 5.0, 6: 2.0, 7: 10.0})


def lift(title="Kyren Williams ruled out for Sunday", chart=CHART, at=AT, index=INDEX, players=None):
    return rules.answer(item(title, at=at), players or [WILLIAMS], [], index=index, chart=lambda: chart, now=NOW)


def test_a_starter_out_lifts_the_next_man():
    got = lift()
    assert got["effect:4"]["choice"] == "up" and got["kind:4"]["choice"] == "depth"
    assert got["chart:4"]["behind"] == 1
    assert got["effect:4"]["confidence"] == rules.CHART_PROB < 0.8  # under the push cutoff


def test_a_starter_the_chart_already_moved_down_lifts_the_new_first():
    chart = {"LAR": [{"name": "Blake Corum", "pos": "RB", "slot": "RB", "order": 1, "injury": None},
                     {"name": "Kyren Williams", "pos": "RB", "slot": "RB", "order": 2, "injury": "Out"}]}
    assert "chart:4" in lift(chart=chart)


def test_a_backup_out_lifts_nobody():
    chart = {"LAR": [{"name": "Blake Corum", "pos": "RB", "slot": "RB", "order": 1, "injury": None},
                     {"name": "Kyren Williams", "pos": "RB", "slot": "RB", "order": 2, "injury": None}]}
    index = Index(by_id=INDEX.by_id, owned={1: 3.0, 4: 40.0})
    assert not [k for k in lift(chart=chart, index=index) if k.startswith("chart:")]


def test_a_hurt_next_man_is_passed_over():
    chart = {"LAR": [{**r, "injury": "Questionable" if r["name"] == "Blake Corum" else None} for r in CHART["LAR"]]}
    got = lift(chart=chart)
    assert "chart:4" not in got and "chart:5" in got


def test_a_wide_receivers_slot_is_his_own():
    got = lift("Puka Nacua ruled out for Sunday", players=[NACUA])
    assert "chart:6" in got and "chart:7" not in got


@pytest.mark.parametrize("kw", [{"chart": {}}, {"at": "2026-10-10T12:00:00+00:00"}, {"title": "Kyren Williams limited"}])
def test_no_chart_old_news_or_a_mild_status_lifts_nobody(kw):
    assert not [k for k in lift(**kw) if k.startswith("chart:")]


def test_a_named_player_keeps_his_own_answer():
    corum = {"player_id": 4, "name": "Blake Corum", "pos": "RB", "team": "LAR"}
    got = lift("Kyren Williams ruled out for Sunday", players=[WILLIAMS, corum])
    assert "chart:4" not in got


def test_the_chart_is_read_only_when_a_lift_is_due():
    def boom():
        raise AssertionError("read the chart")
    rules.answer(item("Kyren Williams limited"), [WILLIAMS], [], index=INDEX, chart=boom, now=NOW)


def test_same_input_same_answers():
    assert lift() == lift()


def test_the_writer_is_told_a_lifted_player_comes_from_the_chart():
    jev = {"chart:4": {"type": "chart", "behind": 1}}
    story = {"id": 1, "status": "live", "rev": 1, "written_rev": 0,
             "items": [{"id": 1, "title": "Kyren Williams ruled out", "text": "", "source_id": "site:LAR", "published_at": AT,
                        "player_ids": [1], "jev": jev}],
             "players": [{"player_id": 1, "effect": "down", "kind": "injury"}, {"player_id": 4, "effect": "up", "kind": "depth"}]}
    p = write.prompt([story], INDEX)
    assert "Blake Corum, RB, LAR; effect up (▲), kind depth; next on the depth chart behind Kyren Williams" in p
    assert "disagree" not in p
