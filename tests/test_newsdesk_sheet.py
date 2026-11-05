"""Which of a player's stories an outlook's fact sheet reads (`newsdesk.sheet`)."""
from ffl.newsdesk import sheet

PID, NAME = 1, "Lamar Jackson"


def item(i, source="site:BAL", title="", text="", ext="", url="", withdrawn=None):
    return {"id": i, "source_id": source, "ext_id": ext or str(i), "url": url or f"https://x/{i}",
            "published_at": f"2026-11-01T{i % 24:02d}:00:00", "title": title, "text": text, "withdrawn_at": withdrawn}


def story(sid, headline="", effect="none", items=None, overview=""):
    return {"id": sid, "headline": headline, "overview": overview, "items": items or [item(sid, title=headline)],
            "players": [{"player_id": PID, "effect": effect, "reason": None}]}


def excerpt(sid, headline, pid=PID):
    return story(sid, headline, items=[item(sid, source="pod:BAL", title="HR1", text="talk", ext=f"ep{sid}#{pid}")])


def ids(got):
    return [s["id"] for s in got]


def test_a_crowd_of_mentions_never_pushes_out_his_news():
    mentions = [story(i, f"Flowers stat line {i}", items=[item(i, title=f"Flowers stat line {i}")]) for i in range(100, 130)]
    ir = story(5, "Lamar Jackson headed to IR", "down")
    full, _ = sheet.choose(mentions + [ir], PID, NAME)
    assert ids(full) == [5]


def test_his_arrows_are_capped_so_old_ones_leave_room_for_what_is_current():
    arrows = [story(i, f"Lamar Jackson practice note {i}", "up") for i in range(50, 45, -1)]
    text = [story(i, f"Lamar Jackson text number {i}") for i in range(40, 33, -1)]
    full, _ = sheet.choose(arrows + text, PID, NAME)
    assert len(full) == sheet.KEEP
    assert [s["id"] for s in full if s["players"][0]["effect"] != "none"] == [50, 49, 48], "the three newest arrows"
    assert [s["id"] for s in full if s["players"][0]["effect"] == "none"] == [40, 39, 38, 37, 36]


def test_spare_slots_go_to_older_arrows_and_the_sheet_stays_newest_first():
    arrows = [story(i, f"Lamar Jackson note {i}", "up") for i in range(10, 5, -1)]
    newer = story(20, "Lamar Jackson text")
    assert ids(sheet.choose([newer] + arrows, PID, NAME)[0]) == [20, 10, 9, 8, 7, 6]


def test_a_story_is_about_him_by_its_written_headline_or_his_own_rotowire_note():
    post = story(1, "Zay Flowers finishes without a catch", items=[item(1, title="Zay Flowers, with Lamar Jackson out, had no catches")])
    note = story(2, "Head coach said Monday he has a sore thumb",
                 items=[item(2, source="rotowire", url=f"https://www.espn.com/nfl/player/_/id/{PID}", title="Coach said Monday")])
    other = story(3, "Hill rushed three times", items=[item(3, source="rotowire", url="https://www.espn.com/nfl/player/_/id/2", title="Hill")])
    unwritten = story(4, "", items=[item(4, title="Lamar Jackson will have surgery")])
    assert ids(sheet.choose([post, note, other, unwritten], PID, NAME)[0]) == [2, 4]


def test_only_an_excerpt_cut_for_him_counts_and_a_repeat_of_what_is_kept_does_not():
    mine = excerpt(1, "Hosts say Lamar Jackson is out for the season")
    theirs = excerpt(2, "Hosts debate the Ravens' cap", pid=99)
    repeat = excerpt(3, "Hosts say Lamar Jackson is out for the season again")
    assert ids(sheet.choose([mine, theirs, repeat], PID, NAME)[0]) == [1]


def test_text_about_him_comes_before_excerpts():
    excerpts = [excerpt(i, f"Episode topic {i} unique{i}") for i in range(20, 10, -1)]
    text = [story(i, f"Lamar Jackson text {i}") for i in range(9, 2, -1)]
    full, _ = sheet.choose(excerpts + text, PID, NAME)
    assert sum(s["items"][0]["source_id"].startswith("pod:") for s in full) == 1


def test_a_passing_mention_is_only_the_sentences_naming_him():
    s = story(7, "Ravens sign a quarterback", items=[item(
        7, title="Ravens sign a quarterback",
        text="Baltimore signed a veteran. Minter confirmed that Lamar Jackson required thumb surgery and misses the year. Other news.")])
    _, lines = sheet.choose([s], PID, NAME)
    assert [(ln["id"], ln["text"]) for ln in lines] == [(7, "Minter confirmed that Lamar Jackson required thumb surgery and misses the year")]
    assert lines[0]["item"]["id"] == 7


def test_a_line_skips_transcripts_scraps_and_repeats_and_stops_at_the_cap():
    spoken = excerpt(1, "Hosts talk", pid=99)
    spoken["items"][0]["text"] = "Lamar Jackson going down was a big moment for the whole team this year"
    scrap = story(2, "Box score", items=[item(2, title="Box score", text="Lamar Jackson (12 yards).")])
    long = "Lamar Jackson will miss the next few games in Baltimore."
    same = [story(i, f"Report {i}", items=[item(i, title=f"Report {i}", text=long)]) for i in (3, 4)]
    more = [story(i, f"Report {i}", items=[item(i, title=f"Report {i}", text=f"Ravens {i} said Lamar Jackson has a thumb injury {i}.")])
            for i in range(10, 20)]
    _, lines = sheet.choose([spoken, scrap, *same, *more], PID, NAME)
    assert len(lines) == sheet.LINES and [ln["id"] for ln in lines][:2] == [3, 10], "newest first; the same words once"
    assert all(ln["id"] not in (1, 2, 4) for ln in lines)
    assert all(len(ln["text"]) <= sheet.LINE_CHARS for ln in lines)


def test_a_withdrawn_source_counts_for_nothing():
    gone = story(1, "Lamar Jackson headed to IR", "down", items=[item(1, title="Lamar Jackson headed to IR", withdrawn="2026-11-02")])
    assert sheet.choose([gone], PID, NAME) == ([], [])
