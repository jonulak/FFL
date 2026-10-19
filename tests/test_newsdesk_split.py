from ffl.newsdesk import split
from ffl.newsdesk.players import Index

INDEX = Index(by_id={1: {"name": "Puka Nacua", "pos": "WR", "team": "LAR"},
                     2: {"name": "Matthew Stafford", "pos": "QB", "team": "LAR"}},
              by_club={"LAR": [1, 2]}, by_full={"puka nacua": [1], "matthew stafford": [2]})
CHI = Index(by_id={10: {"name": "Caleb Williams", "pos": "QB", "team": "CHI"},
                   11: {"name": "Luther Burden III", "pos": "WR", "team": "CHI"},
                   12: {"name": "Kyle Monangai", "pos": "RB", "team": "CHI"},
                   13: {"name": "Kyle Pitts", "pos": "TE", "team": "ATL"},
                   14: {"name": "Tyler Scott", "pos": "WR", "team": "CHI"},
                   15: {"name": "Kyle Brandt", "pos": "TE", "team": "CHI"},
                   16: {"name": "Baker Mayfield", "pos": "QB", "team": "TB"}},
            by_club={"CHI": [10, 11, 12, 14, 15], "ATL": [13], "TB": [16]},
            by_full={"caleb williams": [10], "luther burden": [11], "kyle monangai": [12], "kyle pitts": [13],
                     "tyler scott": [14], "kyle brandt": [15], "baker mayfield": [16]})


def segs(mentions: dict[int, str], until: int = 500) -> list[dict]:
    """A seg every 10 s of filler; `mentions` puts a line at a given second."""
    return [{"start": float(t), "text": mentions.get(t, "and we talked about the game plan")} for t in range(0, until, 10)]


def test_split_windows_merge_and_offset():
    got = split.by_player(segs({100: "I thought Puka Nacua was great", 140: "and then Nacua caught it",
                                400: "Nacua again on the last drive"}), INDEX, ("LAR",))
    assert [g["player_id"] for g in got] == [1]
    one = got[0]
    assert one["start"] == 70.0
    first, second = one["text"].split(" … ")
    assert first.startswith("and we talked") and "Puka Nacua was great" in first and "then Nacua caught" in first
    assert "Nacua again on the last drive" in second
    assert "Nacua again" not in first


def test_split_one_entry_per_player_by_first_mention():
    got = split.by_player(segs({50: "Matthew Stafford threw it", 200: "to Puka Nacua"}), INDEX, ("LAR",))
    assert [(g["player_id"], g["start"]) for g in got] == [(2, 20.0), (1, 170.0)]


def test_split_window_clamped_at_zero_and_name_across_segs():
    got = split.by_player([{"start": 0.0, "text": "Puka"}, {"start": 3.0, "text": "Nacua looked healthy"}],
                          INDEX, ("LAR",))
    assert got == [{"player_id": 1, "start": 0.0, "text": "Puka Nacua looked healthy"}]


def test_split_no_players():
    assert split.by_player(segs({}), INDEX, ("LAR",)) == []


def test_first_name_matches_in_club():
    got = split.by_player([{"start": 50.0, "text": "Caleb was sharp, and Luther's catch"}], CHI, ("CHI",))
    assert [g["player_id"] for g in got] == [10, 11]
    assert got[0]["start"] == 20.0
    assert split.by_player([{"start": 50.0, "text": "Caleb was sharp"}], CHI, ("ATL",)) == []


def test_first_name_shared_on_club_matches_nobody():
    assert split.by_player([{"start": 0.0, "text": "I thought Kyle ran hard"}], CHI, ("CHI",)) == []


def test_common_first_name_not_matched():
    assert split.by_player([{"start": 0.0, "text": "And Tyler did his job"}], CHI, ("CHI",)) == []


def test_title_player_gets_item_without_mentions():
    segs = [{"start": float(t), "text": "the quarterback is progressing well"} for t in range(0, 300, 10)]
    got = split.by_player(segs, CHI, ("TB",), title="Todd Bowles on Baker Mayfield\u2019s Timeline to Return")
    assert [(g["player_id"], g["start"]) for g in got] == [(16, 0.0)]
    assert got[0]["text"] == " ".join(["the quarterback is progressing well"] * 13)  # 0..120 s

    segs[20] = {"start": 200.0, "text": "Baker is doing everything"}  # common first name, but the title says who
    got = split.by_player(segs, CHI, ("TB",), title="Todd Bowles on Baker Mayfield\u2019s Timeline to Return")
    assert got[0]["start"] == 170.0 and "Baker is doing everything" in got[0]["text"]
