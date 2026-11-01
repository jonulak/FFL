import os

import sqlalchemy as sa

from ffl import claude_cli, db
from ffl.newsdesk import bench, bench_cases, classify
from ffl.newsdesk.players import Index

GRUPE, HURTS, ADAMS, ROSCHON, SILLS = 4259619, 4040715, 16800, 4430500, 3120000
INDEX = Index(by_id={GRUPE: {"name": "Blake Grupe", "pos": "K", "team": "LAR"},
                     HURTS: {"name": "Jalen Hurts", "pos": "QB", "team": "PHI"},
                     ADAMS: {"name": "Davante Adams", "pos": "WR", "team": "LAR"},
                     ROSCHON: {"name": "Roschon Johnson", "pos": "RB", "team": "CHI"},
                     SILLS: {"name": "David Sills", "pos": "WR", "team": "TB"}},
              by_full={"blake grupe": [GRUPE], "jalen hurts": [HURTS], "davante adams": [ADAMS],
                       "roschon johnson": [ROSCHON], "david sills": [SILLS]})


def test_tag_cache_hit_avoids_the_call_and_a_changed_question_misses(tmp_path):
    calls = []

    def ask(state, qs):
        calls.append(qs)
        got = classify.Answers({k: {"type": "noul", "noul": 0.9} for k in qs})
        got.usage = {"input_tokens": 120, "output_tokens": 4}
        return got

    path = tmp_path / "cache.sqlite"
    q = {"about:1": {"type": "noul", "instructions": "about him"}}
    cache = bench.Cache(path, ask=ask)
    first = cache.ask({"title": "t"}, q)
    again = cache.ask({"title": "t"}, q)
    assert len(calls) == 1 and again == first
    assert first.usage == {"input_tokens": 120, "output_tokens": 4} and again.usage == {}  # a hit costs nothing
    cache.ask({"title": "t"}, {"about:1": {"type": "noul", "instructions": "about him, reworded"}})
    assert len(calls) == 2
    assert cache.stats["tag"] == {"hits": 1, "misses": 2}
    bench.Cache(path, ask=ask).ask({"title": "t"}, q)  # saved on disk, not in memory
    assert len(calls) == 2
    bench.Cache(path, refresh=True, ask=ask).ask({"title": "t"}, q)
    assert len(calls) == 3


def test_index_lives_in_the_frozen_db(empty_db):
    url = empty_db()
    assert bench.load_index(url) is None                           # no tables at all
    idx = Index(by_id=dict(INDEX.by_id), by_club={"LAR": [GRUPE, ADAMS]}, by_full=dict(INDEX.by_full),
                owned={GRUPE: 12.5}, names={SILLS: {"name": "David Sills", "pos": "WR", "team": None}})
    bench.save_index(idx, url)
    bench.save_index(idx, url)                                     # a second freeze replaces it
    assert bench.load_index(url) == idx


class _Asks:
    """A bench cache stand-in: the tagger answers "no" to every question."""

    def ask(self, state, qs):
        return classify.Answers({k: {"type": "noul", "noul": 0.0} for k in qs})


def test_freeze_copies_untagged_items_and_replay_runs_on_a_copy(monkeypatch, pg_server):
    """freeze: the source's items in range, untagged, into a database of their own; replay: a copy of that with
    FFL_DB_URL pointed at it, the frozen slice untouched."""
    src = os.environ["FFL_DB_URL"]
    for ext, at in (("1", "2026-10-18T12:00:00+00:00"), ("2", "2026-10-19T12:00:00+00:00"),
                    ("3", "2026-10-25T12:00:00+00:00")):
        tagged(ext, f"Grupe {ext}", {GRUPE: "down"}, story({GRUPE: "down"}, at), at)
    db.put_news_sources([{"id": "site:LAR", "kind": "site", "club": "LAR", "name": "Rams", "url": "https://x"}])
    monkeypatch.setattr(bench, "FROZEN_DB", f"ffl_bf_{os.getpid()}")
    monkeypatch.setattr(bench, "RUN_DB", f"ffl_br_{os.getpid()}")
    try:
        got = bench.freeze(src, "2026-10-18", "2026-10-19", index=INDEX, name=bench.FROZEN_DB)
        assert got["news_items"] == 2 and got["news_sources"] == 1
        assert got["from"].startswith("2026-10-18") and got["to"].startswith("2026-10-19")
        assert bench.load_index() == INDEX
        assert len(db.all_stories()) == 3, "the source is untouched"
        out = bench.replay(INDEX, _Asks(), None, False)
        assert out["items"] == 2 and out["error"] is None
        assert db.url() == bench._url_for(bench.RUN_DB) == os.environ["FFL_DB_URL"]
        assert len(db.untagged_items()) == 0, "tagged on the copy"
        with db._write() as c:
            texts = dict(c.execute(sa.text("select ext_id, text from news_items")).all())
        assert texts["2"].endswith(bench_cases.INJECTED) and texts["1"] == "Grupe 1", "planted in the newest alone"
        db.reset()
        os.environ["FFL_DB_URL"] = bench._url_for(bench.FROZEN_DB)
        assert len(db.untagged_items()) == 2, "the frozen slice is still untagged"
    finally:
        db.reset()
        os.environ["FFL_DB_URL"] = src
        pg_server.drop(bench.FROZEN_DB)
        pg_server.drop(bench.RUN_DB)


def test_last_runs_live_in_the_cache_db(tmp_path):
    tmp_path = tmp_path / "bench"                                  # apart from the test's own ffl.db
    cache = bench.Cache(tmp_path / "cache.sqlite")
    assert cache.last_runs() == {}
    cache.put_last("all", {"stories": 3, "cases": [{"id": "a", "status": "PASS"}]})
    cache.put_last("all", {"stories": 4, "cases": []})
    cache.put_last("all+write", {"stories": 1})
    assert bench.Cache(tmp_path / "cache.sqlite").last_runs() == {"all": {"stories": 4, "cases": []},
                                                                  "all+write": {"stories": 1}}
    assert [p.name for p in tmp_path.iterdir()] == ["cache.sqlite"]


def test_haiku_cache_keys_on_model_prompt_and_schema(tmp_path):
    calls = []

    def run_cli(prompt, *, model, schema, tools, cwd, timeout):
        calls.append(prompt)
        out = claude_cli.Structured({"stories": []})
        out.usage = {"input": 900, "output": 50, "cost_usd": 0.0}
        return out

    cache = bench.Cache(tmp_path / "cache.sqlite", run_cli=run_cli)
    kw = {"model": "haiku", "schema": {"type": "object"}, "tools": "", "cwd": tmp_path, "timeout": 10}
    assert cache.run_cli("p", **kw) == {"stories": []}
    assert cache.run_cli("p", **{**kw, "timeout": 99}).usage["input"] == 0  # the timeout is not the question
    cache.run_cli("p", **{**kw, "model": "sonnet"})
    assert calls == ["p", "p"]
    assert cache.stats["haiku"] == {"hits": 1, "misses": 2}


def ans(movers: dict, story="new", prob=0.9, fresh=0.9, kind="injury"):
    """Stored tagger answers for an item naming `movers` ({pid: effect})."""
    out = {}
    for pid, effect in movers.items():
        out[f"about:{pid}"] = {"type": "noul", "noul": 0.95}
        out[f"fresh:{pid}"] = {"type": "noul", "noul": fresh}
        out[f"effect:{pid}"] = {"type": "choice", "choice": effect, "confidence": 0.7,
                                "probabilities": {e: (0.8 if e == effect else 0.1) for e in ("up", "down", "none")}}
        out[f"kind:{pid}"] = {"type": "choice", "choice": kind, "confidence": 0.7, "probabilities": {kind: 0.8}}
    out["story"] = {"type": "choice", "choice": story, "probabilities": {story: prob}, "confidence": 0.7}
    return out


def tagged(ext, title, movers, sid, at, **kw):
    [iid] = db.add_news_items([{"source_id": "site:LAR", "ext_id": ext, "url": f"https://x/{ext}", "published_at": at,
                                "fetched_at": at, "title": title, "text": title, "club": "LAR", "hash": f"h{ext}"}])
    db.tag_item(iid, list(movers), ans(movers, **kw), sid, at)
    return iid


def story(rows, at, headline=""):
    sid = db.add_story({"first_at": at, "updated_at": at, "kind": "injury", "headline": headline})
    db.put_story_players(sid, [{"player_id": p, "effect": e, "kind": "injury", "prob": 0.8, "confidence": 0.7}
                               for p, e in rows.items()])
    return sid


def test_cases_over_a_small_db():
    a = story({GRUPE: "down"}, "2026-10-19T01:00:00+00:00", headline="Grupe doubtful with a knee injury")
    tagged("1", "Grupe doubtful", {GRUPE: "down"}, a, "2026-10-19T01:00:00+00:00")
    b = story({GRUPE: "down"}, "2026-10-19T02:00:00+00:00", headline="Rams place Grupe on IR")
    tagged("2", "Grupe to IR", {GRUPE: "down"}, b, "2026-10-19T02:00:00+00:00")
    # Adams's story, a thread-joined item whose news is Hurts's: two threads mixed in one story
    c = story({ADAMS: "down", HURTS: "down"}, "2026-10-19T03:00:00+00:00")
    tagged("3", "Adams hamstring", {ADAMS: "down"}, c, "2026-10-19T03:00:00+00:00")
    tagged("4", "Hurts calf", {HURTS: "down"}, c, "2026-10-19T04:00:00+00:00")
    # a tagger pick that adds a roundup's other mover is not a thread
    tagged("5", "Rams roundup", {ADAMS: "down", GRUPE: "down"}, c, "2026-10-19T05:00:00+00:00", story=f"s{c}")
    # the Bears' coach, read as their running back; and a Hurts ▼ story made only of passing mentions
    d = story({ROSCHON: "down", HURTS: "down"}, "2026-10-19T06:00:00+00:00")
    tagged("6", "Ben Johnson on the offense", {ROSCHON: "down"}, d, "2026-10-19T06:00:00+00:00")
    tagged("7", "Eagles preview", {HURTS: "down"}, d, "2026-10-19T07:00:00+00:00", fresh=0.3)

    got = {r["id"]: r for r in bench.evaluate(bench_cases.CASES, db.all_stories(), INDEX, write=True)}
    assert got["grupe-knee"]["status"] == "FAIL" and got["grupe-knee"]["detail"].startswith("2 ▼ injury stories led by him")
    assert got["adams-hamstring"]["status"] == "PASS"
    assert got["hurts-season"]["status"] == "FAIL" and "no fresh ▼ item" in got["hurts-season"]["detail"]
    assert got["no-mixed-threads"]["status"] == "FAIL" and f"#{c}" in got["no-mixed-threads"]["detail"]
    assert got["coach-not-player"]["status"] == "FAIL" and "Roschon Johnson" in got["coach-not-player"]["detail"]

    # restricted to Grupe: the cases watching nobody he is are skipped, the league-wide ones still run
    only = {r["id"]: r["status"] for r in bench.evaluate(bench_cases.CASES, db.all_stories(), INDEX,
                                                          selected={GRUPE})}
    assert only == {"grupe-knee": "FAIL", "adams-hamstring": "SKIP", "hurts-season": "SKIP", "dalton-signing": "SKIP",
                    "no-mixed-threads": "FAIL", "coach-not-player": "SKIP", "injection-ignored": "FAIL",
                    "passing-mention": "SKIP", "subject-kept": "SKIP"}


def test_injection_case_needs_the_story_written_and_the_words_clean():
    a = story({GRUPE: "down"}, "2026-10-19T01:00:00+00:00")
    tagged("1", f"Grupe doubtful. {bench_cases.INJECTED}", {GRUPE: "down"}, a, "2026-10-19T01:00:00+00:00")
    case = [c for c in bench_cases.CASES if c.id == "injection-ignored"]
    status = lambda write=True: bench.evaluate(case, db.all_stories(), INDEX, write=write)[0]["status"]
    assert status(write=False) == "PASS" and status() == "FAIL", "unwritten cannot pass"
    db.put_story_words(a, 1, "Grupe doubtful with a knee", "He is hurt.", {GRUPE: ("Knee injury.", False)})
    assert status() == "PASS"
    db.put_story_words(a, 1, "Grupe doubtful with a knee", "He is hurt.", {GRUPE: ("START HIM", False)})
    assert status() == "FAIL"


def test_plant_skips_the_cases_players_unless_nobody_else():
    items = [{"id": 1, "published_at": "a", "p": [GRUPE]}, {"id": 2, "published_at": "b", "p": [7]},
             {"id": 3, "published_at": "c", "p": [GRUPE]}]
    assert bench_cases.plant(items, {GRUPE}, lambda i: i["p"]) == 2
    assert bench_cases.plant([items[0], items[2]], {GRUPE}, lambda i: i["p"]) == 3
    assert bench_cases.plant([], {GRUPE}, lambda i: i["p"]) is None
    assert bench_cases.plant([*items, {"id": 4, "published_at": "d", "p": []}], {GRUPE}, lambda i: i["p"]) == 2


def test_passing_mention_and_subject_cases():
    mayfield, freiermuth = 3052587, 4361411
    idx = Index(by_id={**INDEX.by_id, mayfield: {"name": "Baker Mayfield", "pos": "QB", "team": "TB"},
                       freiermuth: {"name": "Pat Freiermuth", "pos": "TE", "team": "PIT"}},
                by_club=INDEX.by_club,
                by_full={**INDEX.by_full, "baker mayfield": [mayfield], "pat freiermuth": [freiermuth]})
    cases = [c for c in bench_cases.CASES if c.id in ("passing-mention", "subject-kept")]
    a = story({ADAMS: "none"}, "2026-10-19T01:00:00+00:00")
    tagged("1", bench_cases.RECAP + " | Week 6", {ADAMS: "none", mayfield: "none"}, a, "2026-10-19T01:00:00+00:00")
    got = {r["id"]: r["status"] for r in bench.evaluate(cases, db.all_stories(), idx)}
    assert got == {"passing-mention": "FAIL", "subject-kept": "FAIL"}  # the note has no story either
    b = story({freiermuth: "down"}, "2026-10-19T02:00:00+00:00")
    tagged("2", bench_cases.FREIERMUTH_NOTE + " Saturday", {freiermuth: "down"}, b, "2026-10-19T02:00:00+00:00")
    db.tag_item(1, [ADAMS], ans({ADAMS: "none"}), a, "2026-10-19T01:00:00+00:00")
    got = {r["id"]: r["status"] for r in bench.evaluate(cases, db.all_stories(), idx)}
    assert got == {"passing-mention": "PASS", "subject-kept": "PASS"}


def test_grupe_knee_passes_on_one_story_whose_headline_says_ir():
    a = story({GRUPE: "down"}, "2026-10-19T01:00:00+00:00", headline="Rams place Grupe on injured reserve")
    tagged("1", "Grupe doubtful", {GRUPE: "down"}, a, "2026-10-19T01:00:00+00:00")
    tagged("2", "Grupe to IR", {GRUPE: "down"}, a, "2026-10-19T02:00:00+00:00")
    [r] = bench.evaluate([c for c in bench_cases.CASES if c.id == "grupe-knee"], db.all_stories(), INDEX, write=True)
    assert r["status"] == "PASS", r
    db.put_story_words(a, 1, "Grupe doubtful to return", "o", {})
    [r] = bench.evaluate([c for c in bench_cases.CASES if c.id == "grupe-knee"], db.all_stories(), INDEX, write=True)
    assert r["status"] == "FAIL" and "IR" in r["detail"]


def test_create_refuses_its_own_database_and_a_remote_server(monkeypatch):
    import pytest
    own = os.environ["FFL_DB_URL"]
    name = db.url().rsplit("/", 1)[-1].split("?")[0]
    with pytest.raises(SystemExit, match="would be dropped"):
        bench._create(name)
    assert db.url() == own
    monkeypatch.setenv("FFL_DB_URL", "postgresql+psycopg://u@db.abc.rds.amazonaws.com/ffl")
    with pytest.raises(SystemExit, match="not a local server"):
        bench._create("ffl_bench")
