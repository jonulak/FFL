"""The two-user fixture (ffl-soc.6.2.3): A is TEST_USER (team 7 in the home league, their default); B holds team 3 in
the same league and a league of their own A is not in (their default). C co-manages A's team (7): what ESPN keeps per
team (the claim plan, pending claims, seats) they share with A; nothing else."""
from dataclasses import dataclass, replace

from fastapi.testclient import TestClient

from ffl import api, auth, db, leagues
from tests.conftest import TEST_USER
from tests.home import HOME_KEY, home_league

B_ONLY = leagues.LeagueRef("espn:999", "espn", "999", 2026, "B's own", 4, None, None)


@dataclass(frozen=True)
class Two:
    a: leagues.LeagueRef         # A in the home league
    b: leagues.LeagueRef         # B in the home league
    b_only: leagues.LeagueRef    # B in their own league
    c: leagues.LeagueRef         # C, A's co-manager, in the home league

    @property
    def a_id(self) -> int:
        return self.a.user_id

    @property
    def b_id(self) -> int:
        return self.b.user_id


def make() -> Two:
    b_id = db.create_user("b@example.com", "B")
    b_only = leagues.register(replace(B_ONLY, is_default=True), user_id=b_id)
    leagues.join(HOME_KEY, "b@example.com", team_id=3)
    db.create_user("c@example.com", "C")
    c = leagues.join(HOME_KEY, "c@example.com", team_id=7, default=True)
    return Two(home_league(), leagues.membership(b_id, HOME_KEY), b_only, c)


def act_as(monkeypatch, user_id: int) -> None:
    """Every request from here passes as `user_id` (a row in users)."""
    user = db.user_row(user_id)
    monkeypatch.setattr(auth, "decide", lambda *a, **k: (0, user))


def client() -> TestClient:
    return TestClient(api.app)


assert TEST_USER["id"] == 0  # A's id; B and C get theirs from the users sequence
