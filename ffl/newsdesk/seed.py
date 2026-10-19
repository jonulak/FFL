"""The news source registry: every text source the collectors poll, upserted at the top of each run so a
fresh DB needs no manual step. Task 5's media sources (yt:, pod:) are added to SOURCES beside these.

Team-site feeds checked 2026-10-19: all 32 answer at www.<domain>/rss/news, but the Cowboys' and the
Commanders' newest items are from 2018 and 2022 (feeds abandoned), so they are registered inactive.
Bluesky handles were each checked with app.bsky.actor.getProfile on 2026-10-19 and posted that month,
except where noted; the Chargers and the Falcons have no beat writer posting on Bluesky (only a fan blog
for the Falcons), so those clubs have none.
"""
from __future__ import annotations

from espn_api.football.constant import PRO_TEAM_MAP

from .. import db

SITES = {
    "ARI": "azcardinals.com", "ATL": "atlantafalcons.com", "BAL": "baltimoreravens.com", "BUF": "buffalobills.com",
    "CAR": "panthers.com", "CHI": "chicagobears.com", "CIN": "bengals.com", "CLE": "clevelandbrowns.com",
    "DAL": "dallascowboys.com", "DEN": "denverbroncos.com", "DET": "detroitlions.com", "GB": "packers.com",
    "HOU": "houstontexans.com", "IND": "colts.com", "JAX": "jaguars.com", "KC": "chiefs.com", "LV": "raiders.com",
    "LAC": "chargers.com", "LAR": "therams.com", "MIA": "miamidolphins.com", "MIN": "vikings.com",
    "NE": "patriots.com", "NO": "neworleanssaints.com", "NYG": "giants.com", "NYJ": "newyorkjets.com",
    "PHI": "philadelphiaeagles.com", "PIT": "steelers.com", "SF": "49ers.com", "SEA": "seahawks.com",
    "TB": "buccaneers.com", "TEN": "tennesseetitans.com", "WSH": "commanders.com",
}
STALE_SITES = {"DAL", "WSH"}  # newest item more than 30 days old on 2026-10-19
ESPN_TEAM_IDS = {abbrev: tid for tid, abbrev in PRO_TEAM_MAP.items() if abbrev in SITES}

# (handle, club or None for a national reporter, name)
BLUESKY = [
    ("rapsheet.bsky.social", None, "Ian Rapoport"),
    ("tompelissero.bsky.social", None, "Tom Pelissero"),
    ("cameronwolfe.bsky.social", None, "Cameron Wolfe"),  # newest post of his own 2026-04-14
    ("jourdanrodrigue.bsky.social", None, "Jourdan Rodrigue"),
    ("darrenurban.bsky.social", "ARI", "Darren Urban"),
    ("giana-jade.bsky.social", "BAL", "Giana Han"),
    ("joebuscaglia.bsky.social", "BUF", "Joe Buscaglia"),
    ("mattparrino.bsky.social", "BUF", "Matt Parrino"),
    ("mikekayefootball.bsky.social", "CAR", "Mike Kaye"),
    ("kfishbain.bsky.social", "CHI", "Kevin Fishbain"),
    ("bbaby41.bsky.social", "CIN", "Ben Baby"),  # last post 2026-10-04
    ("ashleybastock42.bsky.social", "CLE", "Ashley Bastock"),
    ("tonygrossi.bsky.social", "CLE", "Tony Grossi"),
    ("saadyousuf126.bsky.social", "DAL", "Saad Yousuf"),
    ("parkerjgabriel.bsky.social", "DEN", "Parker Gabriel"),
    ("troyrenck.bsky.social", "DEN", "Troy Renck"),
    ("davebirkett.bsky.social", "DET", "Dave Birkett"),
    ("ben-raven.bsky.social", "DET", "Ben Raven"),
    ("byjbh.bsky.social", "GB", "Jason B. Hirschhorn"),
    ("aaronwilsonnfl.bsky.social", "HOU", "Aaron Wilson"),
    ("joelaerickson.bsky.social", "IND", "Joel A. Erickson"),
    ("jakearthurnfl.bsky.social", "IND", "Jake Arthur"),
    ("demetrius.bsky.social", "JAX", "Demetrius Harvey"),
    ("jessenewell.bsky.social", "KC", "Jesse Newell"),
    ("bynatetaylor.bsky.social", "KC", "Nate Taylor"),
    ("paulhgutierrez.bsky.social", "LV", "Paul Gutierrez"),
    ("adamgrosbard.bsky.social", "LAR", "Adam Grosbard"),
    ("nateatkins.bsky.social", "LAR", "Nate Atkins"),
    ("ml-j.bsky.social", "MIA", "Marcel Louis-Jacques"),
    ("bengoessling.bsky.social", "MIN", "Ben Goessling"),
    ("kevinseifert.bsky.social", "MIN", "Kevin Seifert"),
    ("mikereiss.bsky.social", "NE", "Mike Reiss"),
    ("christopherprice.bsky.social", "NE", "Christopher Price"),
    ("nickunderhill.bsky.social", "NO", "Nick Underhill"),
    ("evanbarnes.bsky.social", "NYG", "Evan Barnes"),
    ("edvalentine.bsky.social", "NYG", "Ed Valentine"),
    ("antwanstaley.bsky.social", "NYJ", "Antwan Staley"),
    ("timmcmanus42.bsky.social", "PHI", "Tim McManus"),
    ("jimmykempski.bsky.social", "PHI", "Jimmy Kempski"),
    ("mikedefabo.bsky.social", "PIT", "Mike DeFabo"),
    ("mattbarrows.bsky.social", "SF", "Matt Barrows"),
    ("mattmaiocco.bsky.social", "SF", "Matt Maiocco"),
    ("johnpboyle.bsky.social", "SEA", "John Boyle"),
    ("jennalaine.bsky.social", "TB", "Jenna Laine"),
    ("jwyattsports.bsky.social", "TEN", "Jim Wyatt"),
    ("tashanreed.bsky.social", "WSH", "Tashan Reed"),
]

SOURCES = (
    [{"id": f"site:{club}", "kind": "site", "club": club, "name": domain,
      "url": f"https://www.{domain}/rss/news", "active": club not in STALE_SITES} for club, domain in SITES.items()]
    + [{"id": f"espn:{club}", "kind": "espn", "club": club, "name": f"ESPN {club}",
        "url": f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/news?team={ESPN_TEAM_IDS[club]}"}
       for club in SITES]
    + [{"id": "pft", "kind": "pft", "club": None, "name": "Pro Football Talk",
        "url": "https://profootballtalk.nbcsports.com/feed/"},
       {"id": "rotowire", "kind": "rotowire", "club": None, "name": "Rotowire",
        "url": "https://site.api.espn.com/apis/fantasy/v2/games/ffl/news/players"}]
    + [{"id": f"bsky:{handle}", "kind": "bluesky", "club": club, "name": name,
        "url": f"https://bsky.app/profile/{handle}"} for handle, club, name in BLUESKY]
)


def ensure() -> int:
    """Register SOURCES (a re-run refreshes them and keeps their health). Returns how many."""
    db.put_news_sources(SOURCES)
    return len(SOURCES)
