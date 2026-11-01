from itertools import product
from ffl import claims as C


def R(pid, slot="BE", droppable=True, locked=False, injury="INJURY_RESERVE"):
    return {"player_id": pid, "name": f"R{pid}", "slot": slot, "droppable": droppable, "locked": locked, "injury": injury}


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
    assert pairs(C.expand([g], st, now=[])) == [(101, 1), (101, 2), (103, 1), (103, 2)]


def test_shared_cut_is_each_groups_row_until_an_add_drops_him():
    gs = [{"targets": [101], "cuts": [1], "open": 0}, {"targets": [201], "cuts": [1, 2], "open": 0}]
    assert pairs(C.expand(gs, {})) == [(101, 1), (201, 1), (201, 2)]
    st = {101: FA, 201: FA}
    assert pairs(C.plan_now(gs, st)) == [(101, 1), (201, 2)]
    assert C.expand(gs, {101: FA}) == [{"group": 1, "add": 201, "drop": 2}]


# ESPN's processing, as a real waiver run showed it: claims in order; a claim is skipped when its add
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


def errs(gs, roster=ROSTER, pool=frozenset({101, 102, 103, 201, -16020}), size=17, status=None, instant_adds=True):
    return [e["reason"] for e in C.validate(gs, roster, set(pool), size, status or {}, instant_adds)]


def test_valid_plan_has_no_errors():
    assert errs([{"targets": [101, 102], "cuts": [1], "open": 1}]) == []


def test_cut_shared_across_groups_allowed_but_not_twice_in_one():
    gs = [{"targets": [101], "cuts": [1], "open": 0}, {"targets": [201], "cuts": [1], "open": 0}]
    assert errs(gs) == []
    assert any("listed twice" in r for r in errs([{"targets": [101], "cuts": [1, 1], "open": 0}]))


def test_an_undesignated_ir_player_blocks_every_plan_but_not_a_withdrawal():
    roster = ROSTER[:-1] + [R(99, "IR", injury="ACTIVE")]
    g = [{"targets": [101], "cuts": [1], "open": 0}]
    block = [e for e in C.validate(g, roster, {101}, 17, {}) if e["reason"] == C.IR_BLOCK]
    assert block == [{"group": None, "player_id": 99, "reason": C.IR_BLOCK}]
    assert C.validate([], roster, {101}, 17, {}) == [], "an empty plan only withdraws claims: no add for ESPN to refuse"
    assert errs(g, roster=ROSTER[:-1] + [R(99, "IR", injury="QUESTIONABLE")]) == [], "any designation keeps IR legal"


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


def test_target_another_team_took_names_the_team():
    g = [{"targets": [777], "cuts": [1], "open": 0}]
    reasons = [e["reason"] for e in C.validate(g, ROSTER, {101}, 17, {}, owners={777: "Hawks"})]
    assert reasons == ["taken by Hawks; remove him from the group"]


def test_target_already_on_roster():
    assert "already on your roster" in errs([{"targets": [5], "cuts": [1], "open": 0}])


def test_instant_adds_off_rejects_every_free_agent_target():
    g = [{"targets": [101, 102], "cuts": [1, 2], "open": 0}]
    off = errs(g, status={101: FA}, instant_adds=False)
    assert off == [C.INSTANT_OFF] and "FFL_INSTANT_ADDS" in off[0]     # 101 only: 102 is a waiver claim
    assert errs(g, status={101: FA}) == []                         # on: the same plan is fine


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


def test_decision_row_and_free_adds():
    names = {101: "Addison", 102: "Mims", 103: "Doubs", 1: "Shaheed", 2: "Lockett"}
    r = C.decision_row([{"targets": [101, 102, 103], "cuts": [1, 2], "open": 0}], names, {102: FA}, 6, 2)
    assert (r["kind"], r["title"], r["week"], r["rationale"]) == ("claims", "Week 6 waiver claims", 6, None)
    assert r["decision"] == "(1) get 2 of 3: Addison, Mims (FA, added now), Doubs; cut Shaheed, Lockett"
    assert r["facts"]["submits"] == 2 and r["player_ids"] == [101, 102, 103, 1, 2]
    adds = C.free_add_rows([{"group": 0, "add": 102, "drop": 1}], names, 6)
    assert [(a["kind"], a["title"], a["player_ids"], a["rationale"]) for a in adds] == [
        ("free_add", "Week 6 add: Mims, drop Shaheed", [102, 1], None)]
    assert C.free_add_rows([{"group": 0, "add": 102, "drop": None}], names, 6)[0]["title"] == "Week 6 add: Mims"


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

    def reorder_claims(self, ids):
        self.reordered = list(ids)
        rank = {i: n for n, i in enumerate(ids)}
        self.p.sort(key=lambda c: rank.get(c["id"], len(rank)))

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
    assert r["match"] is True   # the outside claim was kept on purpose: ours are all there, in order


def test_run_submit_kept_outside_does_not_hide_a_wrong_list():
    class Swapped(FakeW):  # ESPN holds ours in the wrong order, behind a kept outside claim
        def pending_claims(self):
            p = [dict(c) for c in self.p]
            return [p[0], *reversed(p[1:])] if len(p) > 2 else p
    w = Swapped([{"id": "old", "add": 9, "drop": 1}])
    r = C.run_submit(G, {}, w.pending_claims(), set(), False, w)
    assert r["error"] is None and r["match"] is False


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


def test_run_submit_shared_cut_dropped_now_leaves_later_groups():
    g = [{"targets": [101], "cuts": [1], "open": 0}, {"targets": [201, 202], "cuts": [1, 2], "open": 0}]
    w = FakeW([])
    r = C.run_submit(g, {101: "FREEAGENT"}, [], set(), False, w)
    assert w.adds == [(101, 1)] and [(c["add"], c["drop"]) for c in r["pending"]] == [(201, 2), (202, 2)] and r["match"]


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


def test_consume_shared_cut_leaves_every_group():
    g = [{"targets": [101], "cuts": [1], "open": 0}, {"targets": [201], "cuts": [1, 2], "open": 0}]
    assert C.consume(g, [{"group": 0, "add": 101, "drop": 1}]) == [{"targets": [201], "cuts": [2], "open": 0}]


def test_consume_open_spot_row_and_empty_group_dropped():
    g = [{"targets": [101], "cuts": [], "open": 1}, {"targets": [102, 103], "cuts": [1], "open": 0}]
    out = C.consume(g, [{"group": 0, "add": 101, "drop": None}])
    assert out == [{"targets": [102, 103], "cuts": [1], "open": 0}]


def test_consume_dst_negative_ids():
    g = [{"targets": [-16020, 101], "cuts": [-16021], "open": 1}]
    assert C.consume(g, [{"group": 0, "add": -16020, "drop": -16021}]) == [{"targets": [101], "cuts": [], "open": 1}]


def test_run_submit_numbers_the_claims_outside_first():
    w = FakeW([{"id": "old", "add": 9, "drop": 1}])
    r = C.run_submit(G, {}, w.pending_claims(), set(), False, w)
    assert w.reordered == ["old", "n1", "n2"] and r["match"]


def test_unnumbered_read_back_is_never_a_match():
    class Unnumbered(FakeW):
        def pending_claims(self):
            return [{**c, "priority": None} for c in self.p]

    w = Unnumbered([])
    r = C.run_submit(G, {}, [], set(), False, w)
    assert [c["add"] for c in r["pending"]] == [101, 102] and r["match"] is False


def test_retire_after_the_run():
    plan = {"week": 5, "groups": [{"targets": [101], "cuts": [1], "open": 0}],
            "submits": [{"at": "2026-10-06T21:50:25", "claims": [], "ids": []}], "saved_at": "2026-10-06T21:50:25"}
    out = C.retire(plan, "2026-10-07T03:31:23")
    assert out["groups"] == [] and out["submits"] == []
    assert out["retired"] == [{"run": "2026-10-07T03:31:23", "groups": plan["groups"], "submits": plan["submits"]}]
    assert C.retire(out, "2026-10-07T03:31:23") == out, "idempotent"


def test_retire_keeps_a_plan_saved_after_the_run_or_never_sent():
    g = [{"targets": [101], "cuts": [1], "open": 0}]
    sub = [{"at": "2026-10-06T21:50:25", "claims": [], "ids": []}]
    assert C.retire({"week": 5, "groups": g, "submits": sub, "saved_at": "2026-10-07T05:00:00"}, "2026-10-07T03:31:23")["groups"] == g
    assert C.retire({"week": 5, "groups": g, "submits": []}, "2026-10-07T03:31:23")["groups"] == g, "never sent: not spent"
    assert C.retire({"week": 5, "groups": g, "submits": sub}, None)["groups"] == g, "unknown run: keep"
    assert C.retire({"week": 5, "groups": g, "submits": sub}, "2026-10-07T03:31:23")["groups"] == [], "legacy file: last submit"


# Position caps (settings position_limits): R1-R3 are TEs on a cap of 3, R4 a WR; 101/102 TE targets, 103 a WR.
CAP_POS = {1: "TE", 2: "TE", 3: "TE", 4: "WR", 101: "TE", 102: "TE", 103: "WR"}
CAP_ROSTER = [{**r, "pos": CAP_POS.get(r["player_id"], "RB")} for r in ROSTER]


def cap_errs(gs, limits={"TE": 3}):
    return [e["reason"] for e in C.validate(gs, CAP_ROSTER, {101, 102, 103}, 17, {}, pos=CAP_POS, limits=limits)]


def test_at_a_cap_a_target_needs_a_cut_of_his_position():
    assert cap_errs([{"targets": [101], "cuts": [4], "open": 0}]) == [C.cap_reason("TE", 3, 3)]
    assert cap_errs([{"targets": [101], "cuts": [4, 1], "open": 0}]) == []   # a TE cut lets him land
    assert cap_errs([{"targets": [103], "cuts": [4], "open": 0}]) == []      # WR: no cap
    assert cap_errs([{"targets": [101], "cuts": [4], "open": 0}], limits={}) == []   # settings without the key


def test_cap_warnings_count_the_worst_case_in_room_order():
    held2 = [r for r in CAP_ROSTER if r["player_id"] != 3]   # two TEs on a cap of 3
    lim = {"TE": 3}
    one = [{"targets": [101, 102], "cuts": [4, 5], "open": 0}]            # both TEs could land on WR/RB cuts
    assert [w["group"] for w in C.cap_warnings(one, held2, CAP_POS, lim)] == [0]
    swap = [{"targets": [101, 102], "cuts": [1, 4], "open": 0}]           # the first room is a TE: net +1 at worst
    assert C.cap_warnings(swap, held2, CAP_POS, lim) == []
    two = [{"targets": [101], "cuts": [4], "open": 0}, {"targets": [102], "cuts": [5], "open": 0}]
    w = C.cap_warnings(two, held2, CAP_POS, lim)
    assert [x["group"] for x in w] == [1] and "groups above" in w[0]["reason"]
    # at the cap with no TE cut it is validate's error, not a warning too
    assert C.cap_warnings([{"targets": [101], "cuts": [4], "open": 0}], CAP_ROSTER, CAP_POS, lim) == []


def test_position_caps_count_ir_and_skip_missing_settings():
    from ffl import season as S
    roster = [{"pos": "TE", "slot": "BE"}, {"pos": "TE", "slot": "IR"}, {"pos": "QB", "slot": "QB"}]
    assert S.position_caps(roster, {"QB": 4, "TE": 3}) == [
        {"pos": "QB", "n": 1, "limit": 4}, {"pos": "TE", "n": 2, "limit": 3}]
    assert S.position_caps(roster, {}) == []


def test_settings_position_limits_map_position_ids_not_slot_ids():
    from ffl.settings import position_limits
    raw = {"0": 0, "1": 4, "2": 8, "3": 8, "4": 3, "5": 3, "6": -1, "16": 3, "17": -1}
    assert position_limits(raw) == {"QB": 4, "RB": 8, "WR": 8, "TE": 3, "K": 3, "D/ST": 3}
