from itertools import product
from ffl import claims as C


def R(pid, slot="BE", droppable=True, locked=False):
    return {"player_id": pid, "slot": slot, "droppable": droppable, "locked": locked}


ROSTER = [R(i) for i in range(1, 17)] + [R(99, "IR")]   # 16 non-IR on a 17 roster -> 1 open spot
W, FA = "WAIVERS", "FREEAGENT"


def pairs(ms):
    return [(m["add"], m["drop"]) for m in ms]


def test_open_spots_excludes_ir():
    assert C.open_spots(ROSTER, 17) == 1
    assert C.open_spots(ROSTER + [R(17)], 17) == 0


def test_expand_two_of_three():
    g = {"targets": [101, 102, 103], "cuts": [1, 2], "open": 0}
    assert pairs(C.expand([g], {})) == [(101, 1), (101, 2), (102, 1), (102, 2), (103, 1), (103, 2)]


def test_expand_open_spot_first():
    g = {"targets": [101, 102], "cuts": [1], "open": 1}
    assert pairs(C.expand([g], {})) == [(101, None), (101, 1), (102, None), (102, 1)]


def test_expand_groups_in_order():
    gs = [{"targets": [101], "cuts": [1], "open": 0}, {"targets": [201], "cuts": [2], "open": 0}]
    assert [(m["group"], m["add"]) for m in C.expand(gs, {})] == [(0, 101), (1, 201)]


def test_dst_negative_ids():
    g = {"targets": [-16020], "cuts": [-16001], "open": 0}
    assert C.expand([g], {}) == [{"group": 0, "add": -16020, "drop": -16001}]


def test_free_agents_now_then_claims():
    g = {"targets": [101, 102, 103], "cuts": [1, 2], "open": 0}
    st = {102: FA, 101: W, 103: W}
    assert pairs(C.plan_now([g], st)) == [(102, 1)]
    assert pairs(C.expand([g], st)) == [(101, 2), (103, 2)]


def test_free_agents_beyond_capacity_are_not_added():
    g = {"targets": [101, 102, 103], "cuts": [1], "open": 0}
    st = {101: FA, 102: FA, 103: FA}
    assert pairs(C.plan_now([g], st)) == [(101, 1)] and C.expand([g], st) == []


def test_used_override_frees_rows():   # an instant add failed: its row goes back to the claims
    g = {"targets": [101, 102, 103], "cuts": [1, 2], "open": 0}
    st = {102: FA}
    assert pairs(C.expand([g], st, used={0: 0})) == [(101, 1), (101, 2), (103, 1), (103, 2)]


# ESPN's processing, as journal/DECISIONS.md 2026-09-22 records it: claims in order; a claim is skipped when its add
# is gone (taken by someone else, or already mine) or its drop is gone, or it has no drop and the roster is full.
def simulate(claims, taken, roster_ids, open_):
    mine, roster, got = set(), set(roster_ids), []
    for c in claims:
        if c["add"] in taken or c["add"] in mine:
            continue
        if c["drop"] is None:
            if open_ == 0:
                continue
            open_ -= 1
        else:
            if c["drop"] not in roster:
                continue
            roster.discard(c["drop"])
        mine.add(c["add"])
        got.append(c)
    return got


def test_never_more_than_k_and_best_first():
    g = {"targets": [101, 102, 103, 104], "cuts": [1, 2], "open": 1}
    claims = C.expand([g], {})
    for miss in product([False, True], repeat=4):
        taken = {t for t, m in zip(g["targets"], miss) if m}
        got = [c["add"] for c in simulate(claims, taken, [1, 2, 3], 1)]
        avail = [t for t in g["targets"] if t not in taken]
        assert got == avail[:C.capacity(g)], (miss, got)


def test_cap_holds_with_free_agents():
    g = {"targets": [101, 102, 103, 104], "cuts": [1, 2], "open": 0}
    st = {102: FA, 104: FA}
    now = C.plan_now([g], st)
    used_rows = {m["drop"] for m in now}
    rest = C.expand([g], st)
    got = [c["add"] for c in simulate(rest, set(), [r for r in (1, 2) if r not in used_rows], 0)]
    assert [m["add"] for m in now] == [102, 104] and got == []   # both rows went to the instant adds


def test_two_groups_independent_caps():
    gs = [{"targets": [101, 102, 103], "cuts": [1], "open": 0}, {"targets": [201, 202], "cuts": [2], "open": 0}]
    assert [c["add"] for c in simulate(C.expand(gs, {}), set(), [1, 2], 0)] == [101, 201]


def errs(gs, roster=ROSTER, pool=frozenset({101, 102, 103, 201, -16020}), size=17, status=None):
    return [e["reason"] for e in C.validate(gs, roster, set(pool), size, status or {})]


def test_valid_plan_has_no_errors():
    assert errs([{"targets": [101, 102], "cuts": [1], "open": 1}]) == []


def test_shared_cut_rejected():
    gs = [{"targets": [101], "cuts": [1], "open": 0}, {"targets": [201], "cuts": [1], "open": 0}]
    assert any("already a cut in group 1" in r for r in errs(gs))


def test_cut_not_on_roster_and_ir_cut():
    assert any("not on your roster" in r for r in errs([{"targets": [101], "cuts": [555], "open": 0}]))
    assert any("on IR" in r for r in errs([{"targets": [101], "cuts": [99], "open": 0}]))


def test_undroppable_cut_rejected():
    roster = ROSTER[:-2] + [R(16, droppable=False), R(99, "IR")]
    assert any("undroppable" in r for r in errs([{"targets": [101], "cuts": [16], "open": 0}], roster=roster))


def test_locked_cut_only_matters_for_instant_adds():
    roster = ROSTER[:-2] + [R(16, locked=True), R(99, "IR")]
    g = [{"targets": [101], "cuts": [16], "open": 0}]
    assert errs(g, roster=roster) == []                                        # a Wednesday claim: fine
    assert any("game has started" in r for r in errs(g, roster=roster, status={101: FA}))


def test_target_not_in_pool():
    assert any("not in the free-agent pool" in r for r in errs([{"targets": [777], "cuts": [1], "open": 0}]))


def test_duplicate_target():
    gs = [{"targets": [101], "cuts": [1], "open": 0}, {"targets": [101], "cuts": [2], "open": 0}]
    assert any("already a target in group 1" in r for r in errs(gs))


def test_target_is_cut():
    assert any("both a target and a cut" in r for r in errs([{"targets": [1], "cuts": [1], "open": 0}]))


def test_open_spots_one_group_takes_all():
    two_open = ROSTER[:15] + [R(99, "IR")]
    two = [{"targets": [101], "cuts": [], "open": 1}, {"targets": [201], "cuts": [], "open": 1}]
    assert any("only one group" in r for r in errs(two, roster=two_open))
    assert any("all 2 open spots" in r for r in errs([{"targets": [101, 102], "cuts": [1], "open": 1}], roster=two_open))
    assert any("more open spots" in r for r in errs([{"targets": [101], "cuts": [], "open": 2}]))


def test_empty_group_rejected():
    assert any("no targets" in r for r in errs([{"targets": [], "cuts": [1], "open": 0}]))
    assert any("nothing to make room" in r for r in errs([{"targets": [101], "cuts": [], "open": 0}]))


def test_decision_entry():
    e = C.decision_entry([{"targets": [101, 102, 103], "cuts": [1, 2], "open": 0}],
                         {101: "Addison", 102: "Mims", 103: "Doubs", 1: "Shaheed", 2: "Lockett"}, "2026-10-06", {102: FA})
    assert e.startswith("## 2026-10-06 — Waiver claims (from the app)")
    assert "get 2 of 3: Addison, Mims (FA, added now), Doubs; cut Shaheed, Lockett" in e
    assert "**Reasoning:** (add by hand)" in e


class FakeW:
    def __init__(self, pending, fail_claim_on=None, taken=()):
        self.p, self.fail_on, self.n, self.taken, self.adds = list(pending), fail_claim_on, 0, set(taken), []

    def add_free_agent(self, add, drop):
        if add in self.taken:
            raise RuntimeError(f"ESPN 409: {add} taken")
        self.adds.append((add, drop))

    def pending_claims(self):
        return [dict(c) for c in self.p]

    def cancel_claim(self, i):
        self.p = [c for c in self.p if c["id"] != i]

    def submit_claim(self, add, drop):
        self.n += 1
        if self.n == self.fail_on:
            raise RuntimeError("ESPN 500: boom")
        self.p.append({"id": f"n{self.n}", "add": add, "drop": drop})
        return f"n{self.n}"


G = [{"targets": [101, 102], "cuts": [1], "open": 0}]       # waivers only: claims (101,1), (102,1)


def test_run_submit_replaces_app_claims_keeps_outside():
    w = FakeW([{"id": "old", "add": 9, "drop": 1}, {"id": "mine", "add": 8, "drop": 2}])
    r = C.run_submit(G, {}, w.pending_claims(), {"mine"}, False, w)
    assert [c["id"] for c in r["pending"]] == ["old", "n1", "n2"] and r["error"] is None
    assert r["match"] is False   # an outside claim still runs ahead of ours


def test_run_submit_replace_outside_matches():
    w = FakeW([{"id": "old", "add": 9, "drop": 1}])
    r = C.run_submit(G, {}, w.pending_claims(), set(), True, w)
    assert r["match"] and [c["add"] for c in r["pending"]] == [101, 102]


def test_run_submit_partial_failure_reads_back():
    w = FakeW([], fail_claim_on=2)
    r = C.run_submit(G, {}, [], set(), True, w)
    assert not r["match"] and "boom" in r["error"] and [c["add"] for c in r["pending"]] == [101]


def test_submit_order_mismatch():
    cl = [{"group": 0, "add": 101, "drop": 1}, {"group": 0, "add": 102, "drop": 1}]
    assert not C.matches(cl, [{"id": "a", "add": 102, "drop": 1}, {"id": "b", "add": 101, "drop": 1}])


def test_run_submit_free_agent_now_then_claims():
    g = [{"targets": [101, 102, 103], "cuts": [1, 2], "open": 0}]
    w = FakeW([])
    r = C.run_submit(g, {102: "FREEAGENT"}, [], set(), False, w)
    assert w.adds == [(102, 1)] and [(c["add"], c["drop"]) for c in r["pending"]] == [(101, 2), (103, 2)] and r["match"]


def test_run_submit_taken_free_agent_frees_its_row():
    g = [{"targets": [101, 102, 103], "cuts": [1, 2], "open": 0}]
    w = FakeW([], taken={102})
    r = C.run_submit(g, {102: "FREEAGENT"}, [], set(), False, w)
    assert r["now"] == [] and r["now_failed"][0]["add"] == 102 and "taken" in r["now_failed"][0]["error"]
    assert [(c["add"], c["drop"]) for c in r["pending"]] == [(101, 1), (101, 2), (103, 1), (103, 2)]


def test_run_submit_readback_failure_keeps_the_record():
    class W(FakeW):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.reads = 0

        def pending_claims(self):
            self.reads += 1
            if self.reads >= 2:
                raise RuntimeError("ESPN 503: down")
            return super().pending_claims()

    g = [{"targets": [101, 102, 103], "cuts": [1, 2], "open": 0}]
    w = W([])
    r = C.run_submit(g, {102: "FREEAGENT"}, w.pending_claims(), set(), False, w)
    assert r["error"] and "read-back" in r["error"] and r["match"] is False and r["pending"] == []
    assert r["readback_failed"] and len(r["now"]) == 1 and len(r["sent"]) == 2


def test_consume_free_agent_row():
    g = [{"targets": [101, 102, 103], "cuts": [1, 2], "open": 0}]
    assert C.consume(g, [{"group": 0, "add": 102, "drop": 1}]) == [{"targets": [101, 103], "cuts": [2], "open": 0}]
    assert g[0]["cuts"] == [1, 2]   # input untouched


def test_consume_open_spot_row_and_empty_group_dropped():
    g = [{"targets": [101], "cuts": [], "open": 1}, {"targets": [102, 103], "cuts": [1], "open": 0}]
    out = C.consume(g, [{"group": 0, "add": 101, "drop": None}])
    assert out == [{"targets": [102, 103], "cuts": [1], "open": 0}]


def test_consume_dst_negative_ids():
    g = [{"targets": [-16020, 101], "cuts": [-16021], "open": 1}]
    assert C.consume(g, [{"group": 0, "add": -16020, "drop": -16021}]) == [{"targets": [101], "cuts": [], "open": 1}]
