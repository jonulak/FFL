import pytest

from ffl import player_views as pv


def R(pid, add=None, *, pos="RB", injury="ACTIVE", owner=None, mine=False, status="FREEAGENT", team="DET", form=None, **kw):
    return {"player_id": pid, "name": f"P{pid}", "pos": pos, "add": add, "injury": injury, "owner": owner,
            "mine": mine, "status": status, "team": team, "form": form, **kw}


def test_value_reads_top_level_and_form_fields():
    r = R(1, 4.0, week_proj=11.0, ros_adj=90.0, form={"snap_pct": 62})
    assert pv.value(r, "add") == 4.0 and pv.value(r, "wk") == 11.0 and pv.value(r, "ros") == 90.0
    assert pv.value(r, "snap_pct") == 62 and pv.value(R(2), "snap_pct") is None


def test_numeric_filter_keeps_the_threshold_and_drops_a_missing_figure():
    rows = [R(1, form={"snap_pct": 50}), R(2, form={"snap_pct": 49}), R(3, form=None), R(4, form={"snap_pct": None})]
    assert [r["player_id"] for r in pv.filter_players(rows, [{"field": "snap_pct", "op": "ge", "value": 50}])] == [1]
    assert [r["player_id"] for r in pv.filter_players(rows, [{"field": "snap_pct", "op": "le", "value": 49}])] == [2]


def test_filters_combine_and_the_set_filters_bucket():
    rows = [R(1, injury="QUESTIONABLE", team="GB"), R(2, injury="OUT"), R(3, owner={"team_id": 2}),
            R(4, mine=True, owner={"team_id": 7}), R(5, status="WAIVERS"), R(6, status=None)]
    ids = lambda fs: [r["player_id"] for r in pv.filter_players(rows, fs)]  # noqa: E731
    assert ids([{"field": "injury", "op": "in", "value": ["qd"]}]) == [1]
    assert ids([{"field": "injury", "op": "in", "value": ["out"]}]) == [2]
    assert ids([{"field": "avail", "op": "in", "value": ["waivers"]}]) == [5, 6], "a failed status read counts as waivers"
    assert ids([{"field": "avail", "op": "in", "value": ["theirs", "mine"]}]) == [3, 4]
    assert ids([{"field": "team", "op": "in", "value": ["GB"]}, {"field": "injury", "op": "in", "value": ["qd"]}]) == [1]
    assert ids([{"field": "team", "op": "in", "value": ["GB"]}, {"field": "injury", "op": "in", "value": ["healthy"]}]) == []


def test_parse_params():
    o = pv.parse_params(pos="ALL", fa=False, cols="add,snap_pct", f=["snap_pct:ge:50", "team:in:DET,GB"], sort="snap_pct:asc")
    assert o == {"pos": "ALL", "fa": False, "cols": ["add", "snap_pct"],
                 "filters": [{"field": "snap_pct", "op": "ge", "value": 50.0}, {"field": "team", "op": "in", "value": ["DET", "GB"]}],
                 "sort": {"id": "snap_pct", "desc": False}}
    assert pv.parse_params(pos=None, fa=None, cols=None, f=None, sort=None) == {}
    assert pv.parse_params(pos=None, fa=None, cols="", f=None, sort=None) == {"cols": []}
    assert pv.parse_params(pos=None, fa=None, cols=None, f=[""], sort=None) == {"filters": []}, "a bare f= clears a preset's filters"


@pytest.mark.parametrize("kw", [dict(cols="add,nope"), dict(cols="add,add"), dict(f=["snap_pct:gt:5"]), dict(f=["snap_pct:ge:x"]),
                                dict(f=["injury:in:hurt"]), dict(f=["team:in:detroit"]), dict(f=["bogus"]),
                                dict(sort="foo:desc"), dict(sort="add:up"), dict(f=["add:ge:nan"]), dict(f=["add:le:inf"])])
def test_parse_params_refuses_junk(kw):
    with pytest.raises(ValueError):
        pv.parse_params(**{"pos": None, "fa": None, "cols": None, "f": None, "sort": None, **kw})


def test_cap_takes_the_first_150_by_the_sort_missing_last():
    rows = [R(i, add=float(i)) for i in range(200)] + [R(999, add=None)]
    out, capped = pv.cap(rows, {"id": "add", "desc": True})
    assert capped and len(out) == pv.CAP and out[0]["player_id"] == 199 and 999 not in {r["player_id"] for r in out}
    out, capped = pv.cap(rows[:10], {"id": "add", "desc": True})
    assert not capped and len(out) == 10
    many = [R(1, form={"snap_pct": 30}), R(2, form=None), R(3, form={"snap_pct": 10})] + [R(10 + i, form={"snap_pct": 90}) for i in range(150)]
    asc, _ = pv.cap(many, {"id": "snap_pct", "desc": False})
    assert [r["player_id"] for r in asc[:2]] == [3, 1] and 2 not in {r["player_id"] for r in asc}


def test_load_seeds_an_empty_store_and_repairs_a_lost_default():
    assert pv.load(None) == {"presets": [pv.SEED], "default": "waivers"}
    s = {"presets": [{**pv.SEED, "id": "b", "name": "B"}], "default": "gone"}
    assert pv.load(s)["default"] == "b"


def test_resolve_layers_overrides_and_falls_back_on_an_unknown_preset():
    store = pv.load(None)
    v = pv.resolve(store, None, {"pos": "WR"})
    assert v["preset"] == "waivers" and v["name"] == "Waivers" and v["pos"] == "WR" and v["cols"] == pv.SEED["cols"]
    assert pv.resolve(store, "gone", {})["preset"] == "waivers"


def test_store_ops():
    store = pv.load(None)
    store = pv.create(store, {"name": " Breakouts ", "pos": "ALL", "fa": True, "cols": ["snap_pct"], "filters": [],
                              "sort": {"id": "snap_pct", "desc": True}}, "b1")
    assert store["presets"][-1]["id"] == "b1" and store["presets"][-1]["name"] == "Breakouts"
    store = pv.update(store, "b1", {"name": "Usage"})
    assert store["presets"][-1]["name"] == "Usage" and store["presets"][-1]["cols"] == ["snap_pct"]
    store = pv.set_default(store, "b1")
    assert store["default"] == "b1"
    store = pv.delete(store, "b1")
    assert store["default"] == "waivers" and [p["id"] for p in store["presets"]] == ["waivers"]
    store = pv.delete(store, "waivers")
    assert store == {"presets": [pv.SEED], "default": "waivers"}, "the last preset gone: the seed comes back"
    with pytest.raises(KeyError):
        pv.update(store, "nope", {"name": "x"})


@pytest.mark.parametrize("body", [{"name": ""}, {"name": "x" * 41}, {"cols": ["add", "add"]}, {"pos": "FB"}, {"fa": "yes"},
                                  {"filters": [{"field": "nope", "op": "ge", "value": 1}]}, {"sort": {"id": "nope", "desc": True}},
                                  {"filters": [{"field": "add", "op": "ge", "value": float("nan")}]}])
def test_clean_refuses_a_bad_preset(body):
    with pytest.raises(ValueError):
        pv.clean(body, partial=True)


def test_clean_requires_every_field_on_create():
    with pytest.raises(ValueError):
        pv.clean({"name": "x"})
