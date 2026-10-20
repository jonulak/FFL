from ffl import claude_cli, db
from ffl.newsdesk import bench, bench_cases, jev
from ffl.newsdesk.players import Index

GRUPE, HURTS, ADAMS, ROSCHON, SILLS = 4259619, 4040715, 16800, 4430500, 3120000
INDEX = Index(by_id={GRUPE: {"name": "Blake Grupe", "pos": "K", "team": "LAR"},
                     HURTS: {"name": "Jalen Hurts", "pos": "QB", "team": "PHI"},
                     ADAMS: {"name": "Davante Adams", "pos": "WR", "team": "LAR"},
                     ROSCHON: {"name": "Roschon Johnson", "pos": "RB", "team": "CHI"},
                     SILLS: {"name": "David Sills", "pos": "WR", "team": "TB"}},
              by_full={"blake grupe": [GRUPE], "jalen hurts": [HURTS], "davante adams": [ADAMS],
                       "roschon johnson": [ROSCHON], "david sills": [SILLS]})


def test_jev_cache_hit_avoids_the_call_and_a_changed_question_misses(tmp_path):
    calls = []

    def ask(state, qs):
        calls.append(qs)
        got = jev.Answers({k: {"type": "noul", "noul": 0.9} for k in qs})
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
    assert cache.stats["jev"] == {"hits": 1, "misses": 2}
    bench.Cache(path, ask=ask).ask({"title": "t"}, q)  # saved on disk, not in memory
    assert len(calls) == 2
    bench.Cache(path, refresh=True, ask=ask).ask({"title": "t"}, q)
    assert len(calls) == 3


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
    """Stored Jev answers for an item naming `movers` ({pid: effect})."""
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
    # a Jev pick that adds a roundup's other mover is not a thread
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
    assert only == {"grupe-knee": "FAIL", "adams-hamstring": "SKIP", "hurts-season": "SKIP",
                    "no-mixed-threads": "FAIL", "coach-not-player": "SKIP"}


def test_grupe_knee_passes_on_one_story_whose_headline_says_ir():
    a = story({GRUPE: "down"}, "2026-10-19T01:00:00+00:00", headline="Rams place Grupe on injured reserve")
    tagged("1", "Grupe doubtful", {GRUPE: "down"}, a, "2026-10-19T01:00:00+00:00")
    tagged("2", "Grupe to IR", {GRUPE: "down"}, a, "2026-10-19T02:00:00+00:00")
    [r] = bench.evaluate([c for c in bench_cases.CASES if c.id == "grupe-knee"], db.all_stories(), INDEX, write=True)
    assert r["status"] == "PASS", r
    db.put_story_words(a, 1, "Grupe doubtful to return", "o", {})
    [r] = bench.evaluate([c for c in bench_cases.CASES if c.id == "grupe-knee"], db.all_stories(), INDEX, write=True)
    assert r["status"] == "FAIL" and "IR" in r["detail"]
