"""Rescoring tables for tests: the old flat key dict (ppr, pass_td, ...) as settings.rescoring's shape,
one table per rescored position."""
from ffl.settings import RESCORED_POS


def tables(ks: dict, two_pt: float = 2.0, **per_pos: dict) -> dict[str, dict[str, float]]:
    """{pos: table} with every position on `ks`; `per_pos` overrides a position's entries (TE={"rec": 1.5})."""
    base = {"pass_yd": ks.get("pass_yd", 0), "pass_td": ks.get("pass_td", 0), "pass_int": ks.get("int", 0),
            "rush_yd": ks.get("rush_yd", 0), "rush_td": ks.get("rush_td", 0), "rec": ks.get("ppr", 0),
            "rec_yd": ks.get("rec_yd", 0), "rec_td": ks.get("rec_td", 0), "fum_lost": -abs(ks.get("fumble", 0)),
            "pass_2pt": two_pt, "rush_2pt": two_pt, "rec_2pt": two_pt}
    return {p: {**base, **per_pos.get(p, {})} for p in RESCORED_POS}
