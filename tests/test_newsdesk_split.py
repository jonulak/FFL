from ffl.newsdesk import split
from ffl.newsdesk.players import Index

INDEX = Index(by_id={1: {"name": "Puka Nacua", "pos": "WR", "team": "LAR"},
                     2: {"name": "Matthew Stafford", "pos": "QB", "team": "LAR"}},
              by_club={"LAR": [1, 2]}, by_full={"puka nacua": [1], "matthew stafford": [2]})


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
