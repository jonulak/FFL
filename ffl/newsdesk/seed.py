"""The news source registry: every text source the collectors poll, upserted at the top of each run so a
fresh DB needs no manual step.

Team-site feeds checked 2026-10-19: all 32 answer at www.<domain>/rss/news, but the Cowboys' and the
Commanders' newest items are from 2018 and 2022 (feeds abandoned), so they are registered inactive.
Bluesky handles were each checked with app.bsky.actor.getProfile on 2026-10-19 and posted that month,
except where noted; the Chargers and the Falcons have no beat writer posting on Bluesky (only a fan blog
for the Falcons), so those clubs have none.

YouTube channels (yt:) were each checked on 2026-10-19 by the channel's RSS answering with the club's name
and that week's videos. Podcasts (pod:): every club's iTunes podcast search, keeping the NFL's Omny feeds
(org e73c998e…) that carry `<podcast:transcript>` and published that month; one per club, its
press-conference audio where it has one, else its busiest show. The Raiders, the Eagles (newest
transcribed episode 2024) and the Commanders have none. A feed holds every episode ever published
(NE's is 14 MB, 0.9 MB gzipped), so a smaller show is preferred where it carries the same voices.
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
YOUTUBE = {
    "ARI": "UCzNfiKcvNjLHljohEkO83Rg", "ATL": "UCzjCfV3LHyarKbdb-2ScGZg", "BAL": "UCbpj2JAUMb8G_oQQFPeGrhg",
    "BUF": "UCcEvCxUe2Sm5W6MliEUv0vg", "CAR": "UC6vl4XAyO5mZLczhFC8MgpA", "CHI": "UCP0Cdc6moLMyDJiO0s-yhbQ",
    "CIN": "UCnQUpSnJ39KsjZKbT0CprHQ", "CLE": "UCQQQO7Kdo0cu9iEb19qOoYA", "DAL": "UCC0BPKJxAyxjQoRTYbpW0FQ",
    "DEN": "UCDGdBexlDZA8T7hnweWWyow", "DET": "UCv5J06V-ESk5_1uriG65f3w", "GB": "UCJtI-l6La0zniodFtSHYrBg",
    "HOU": "UCa_FcpOBe8G6VAR18RYS-aA", "IND": "UCyYn26HPC4HIedifGnNbjBw", "JAX": "UCsGacW6z0GedR-Wv45SBRZg",
    "KC": "UC-hXefb6XBFSubWz6Ezf_lA", "LV": "UC1es5fp8FEK1L0EgHjCvmtQ", "LAC": "UCUyz_gEY_N-KBU4zjt2s-uQ",
    "LAR": "UCyJ6yZdVUkBvt2vl4R03jcA", "MIA": "UCHUSfEzpSRkUUsRkk_aJwDw", "MIN": "UCcsw_KrB_wg5lQ5nXWR_LFA",
    "NE": "UCMm_V8YjmnZRhToXIqDYDuw", "NO": "UCwuddf1JrodMlc5fYpVlrQA", "NYG": "UCk2FqoG8dN5EAz5WU3A0D7A",
    "NYJ": "UCROj9vBjc4ZW3AL4cd_BjHg", "PHI": "UCaogx6OHpsGg0zuGRKsjbtQ", "PIT": "UChaRXjMDs4ppKfnTPE6Z89w",
    "SF": "UCeIOarQkwmGhimim9cDUTng", "SEA": "UCzkFCRiMcOBeef8xcaqipmw", "TB": "UC0Wwu7r1ybaaR09ANhudTzA",
    "TEN": "UCHBsqVkFraWvtNd1w0Qx4_g", "WSH": "UC2a0ENbCZqIO5C1fWXGXZXA",
}
OMNY = "https://www.omnycontent.com/d/playlist/e73c998e-6e60-432f-8610-ae210140c5b1/{}/podcast.rss"
PODCASTS = {  # club -> (show, program/playlist)
    "ARI": ("The Cardinals Locker Room Show", "1654549e-1459-4e24-95c6-b1fd00dc7618/9bd493f7-9ac1-4f91-bcd8-b1fd00dd45b3"),
    "ATL": ("Atlanta Falcons Podcast Network", "a96bdfcb-0b61-4081-b3cf-aea701838a15/ff492b27-7417-4475-b5df-aea701838a1f"),
    "BAL": ("Ravens Press Pass", "b4ae27ce-5d7a-4ff6-8845-afa40133a202/0f1cf63e-248d-44f6-8dc8-afa40134a88d"),
    "BUF": ("One Bills Live", "81b68f85-0c3c-4cd7-a504-aeac01336f91/6f9db0fa-bb9c-4c3f-b0df-aeac01336f9a"),
    "CAR": ("Panthers News", "7492cc31-e172-4597-bee5-b48701147eee/c9ec5364-a962-4956-b561-b48701147f02"),
    "CHI": ("Chicago Bears Podcasts", "4f703a36-2fa9-4d42-b09a-aeac01327730/b3c8eeec-ff0b-46a5-b7e4-aeac0132773e"),
    "CIN": ("Bengals Booth Podcast", "218b01ba-4e5c-4581-b447-aeac013301e7/d1024af1-554d-4f25-bb78-aeac013301f5"),
    "CLE": ("From the Podium", "3116fea5-73d2-4bcf-b679-b05300f62410/894772cb-850f-421c-a2cd-b05300f6c6fa"),
    "DAL": ("Talkin' Cowboys", "daa09c7f-6c6b-44bd-b906-aeac01489de7/f8250829-ef85-42cf-90ff-aeac01489e03"),
    "DEN": ("Altitude Advantage", "bf3649f7-52e4-4782-ac01-aea8003fc494/1f68ce42-48ed-4c1e-a569-aea8003fc4a7"),
    "DET": ("The Official Detroit Lions Podcast", "a657d858-40ab-4162-be1e-b4c200e90446/25b2a262-73c4-4e95-af39-b4c200e9082c"),
    "GB": ("Packers Unscripted", "3862fdb6-c358-40eb-88b2-aea8004103a7/b8766a57-277c-4d7e-965e-aea8004103b9"),
    "HOU": ("Texans All Access", "178d0781-85e0-4be4-8ff4-aeac0131b45b/85ddc5c0-36f8-48be-b048-aeac0131b485"),
    "IND": ("Indianapolis Colts Podcasts", "879995aa-962b-4721-90c4-aead0111161d/41c299e1-30e0-43fa-b2e0-aead0111162b"),
    "JAX": ("Press Pass", "928d2e4d-54a7-4bcd-9a3f-af1e00f8f46c/9915eb57-fa64-4505-be33-af1e00f94e92"),
    "KC": ("Defending The Kingdom", "5578c9d1-d9f6-4392-8fb1-aeac01366e12/b821b864-b83a-4214-9793-aeac01366e20"),
    "LAC": ("Chargers Podcast Network", "01891723-b9f7-459c-a03c-aeac01364986/46e9a45b-cb89-4c6d-a54a-aeac01364994"),
    "LAR": ("Los Angeles Rams Audio", "f924465c-dd4c-45bc-977f-afc1015c99c3/8c9673c7-f48a-43a5-870b-afc1015d7438"),
    "MIA": ("Drive Time with Travis Wingfield", "4ed207d2-7db7-4799-9aa5-aea90021be63/66d7bdfb-e635-4f56-bf92-aea90021be6c"),
    "MIN": ("Minnesota Vikings Podcast", "2453b881-87fe-4bb9-94b6-aeac0125bd8b/36f5c061-52f9-4081-97ac-aeac0125bd99"),
    "NE": ("Patriots Player & Coach Audio", "fd2053fa-164b-4a47-9d15-aea9013a0962/46c6fc15-f75b-4987-9923-aea9013a097e"),
    "NO": ("Saints HQ", "b65f75d5-8244-40d2-ae4d-b4b9012cf296/b41f2bd6-c07a-42aa-bac7-b4b9012cf708"),
    "NYG": ("Giants Media Pass", "a09aae3f-52cf-45cb-adaf-aea9002566dc/4585ecde-2a1f-4e7f-b93d-aea9002566e5"),
    "NYJ": ("The Official Jets Podcast", "a3d7359f-4386-4b88-98bf-aea900280df2/9ae1d2cb-9d29-45fc-a551-aea900280dfc"),
    "PIT": ("In the Locker Room", "af40d2d3-c871-4a2d-a722-aea000012051/8c0927c1-7add-465c-b316-aea00001206d"),
    "SF": ("49ers: Press Pass", "d275d967-ca19-470d-9dfc-af2c01386a16/b51e2fd8-57aa-477c-8ff0-af2c0138bc7b"),
    "SEA": ("Seahawks Press Pass", "02fc1050-3817-4c1c-89f8-b069015b8f57/fd76bb45-1dca-4740-b99f-b069015d232e"),
    "TB": ("Bucs Press Conferences", "775ad47a-0e2d-4db2-a632-af0a01152de1/bc28f0fc-5a86-4ea1-9c41-af0a01158ddb"),
    "TEN": ("Titans Podcast Network", "31142dcb-0076-4343-b172-aeac0126b451/a3093306-f091-44f3-ab3a-aeac0126b469"),
}
ESPN_TEAM_IDS = {abbrev: tid for tid, abbrev in PRO_TEAM_MAP.items() if abbrev in SITES}

# Shows with no transcript in their feed, transcribed by the stt service (ffl/stt.py) when an install runs it; without
# one their episodes queue and nothing is read. Locked On, one daily show per club (feed paths off iTunes' search,
# checked 2026-11-04: every one published that week), and the national fantasy shows the week's analysis is argued
# on. A national show has no club, so only a full name matches a player in it (split.by_player).
LOCKED_ON = "https://rss.pdrl.fm/{}/feeds.simplecast.com/{}"
LOCKED_ON_PATHS = {
    "ARI": "21b2e1/SRxnlfNZ", "ATL": "d31f4c/l96cnojt", "BAL": "e33f24/CxbuJ6nL", "BUF": "8c06ff/LIaoLB9Y",
    "CAR": "c2438b/3JTeh8Y2", "CHI": "16e145/yvyasWU5", "CIN": "8b4258/J1A29rxl", "CLE": "bc2558/KHgFdYZS",
    "DAL": "3e3416/4NoEmSg7", "DEN": "ed7d56/f6CBcmwn", "DET": "a0cc13/ASw0G4io", "GB": "5b095f/FkBDUQLt",
    "HOU": "de6246/eO87ZWbO", "IND": "9766e6/PpDN4Fbf", "JAX": "5b6f8d/BgZ7Brxt", "KC": "194e78/9czLVzrJ",
    "LV": "427f18/FW_wEF1P", "LAC": "d1cedf/A8M6ytRb", "LAR": "3fd73b/a4Q6EDeU", "MIA": "781205/f8EFrtWR",
    "MIN": "dbd095/sa0QprVo", "NE": "b4173b/wbru9pmV", "NO": "83ac39/aKgoC4uX", "NYG": "da64a7/Iu3ZtP7n",
    "NYJ": "118578/x5WGS9vX", "PHI": "32c77c/yy6iqCPY", "PIT": "cd1f3b/y_l5uReM", "SF": "f9f75c/P83yLSb_",
    "SEA": "099859/nxLgqRTF", "TB": "ca31e9/mOWpQkH2", "TEN": "54f99a/ZHgqzawD", "WSH": "7c3048/psq7p3Ar",
}
NATIONAL_PODCASTS = [  # (slug, show, feed); The Late-Round show is an Omny feed with transcripts: no stt needed
    ("fantasy-footballers", "Fantasy Footballers", "https://feeds.simplecast.com/sw7PGWfw"),
    ("fantasy-football-today", "Fantasy Football Today",
     "https://rss.amperwave.net/v2/feed/audacynetwork/38ad78ba12adc2acec34b47b1a455b85"),
    ("yahoo-fantasy-forecast", "Yahoo Fantasy Forecast with Matt Harmon", "https://feeds.simplecast.com/s3ulbOAs"),
    ("establish-the-run", "Establish The Run Fantasy Football", "https://anchor.fm/s/60e53980/podcast/rss"),
    ("the-audible", "The Audible (Footballguys)", "https://theaudible.libsyn.com/rss"),
    ("ringer-fantasy-football", "The Ringer Fantasy Football Show",
     "https://feeds.megaphone.fm/ringer-fantasy-football-show"),
    ("pff-nfl", "The PFF NFL Podcast", "https://feeds.simplecast.com/U644ZfMt"),
    ("reception-perception", "Reception Perception: The Show",
     "https://rss.amperwave.net/v2/feed/audacynetwork/reception-perception-the-show"),
    ("fantasy-focus", "Fantasy Focus Football", "https://feeds.megaphone.fm/ESP7699980268"),
    ("late-round", "The Late-Round Fantasy Football Podcast",
     OMNY.format("0a233618-d694-4c81-a139-b34e00f98fb1/5747cc82-33a9-497c-8353-b34e00f98fbe")),
    ("rotoworld", "Rotoworld Football Show",
     "https://www.omnycontent.com/d/playlist/006cbbeb-1b7a-4729-b380-b3df0127e8db/78bf40ca-35a9-4977-bef5-b3df0134b699/"
     "170962aa-f18f-494e-a969-b3df0134b6a2/podcast.rss"),
]

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
    + [{"id": f"yt:{club}", "kind": "youtube", "club": club, "name": f"YouTube {club}",
        "url": f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"} for club, cid in YOUTUBE.items()]
    + [{"id": f"pod:{club}", "kind": "podcast", "club": club, "name": show, "url": OMNY.format(path)}
       for club, (show, path) in PODCASTS.items()]
    # the `pod:` prefix is what the tagger keys a transcript item's own player on (tag.MEDIA)
    + [{"id": f"pod:{club}/lockedon", "kind": "podcast", "club": club, "name": f"Locked On {club}",
        "url": LOCKED_ON.format(*path.split("/"))} for club, path in LOCKED_ON_PATHS.items()]
    + [{"id": f"pod:national/{slug}", "kind": "podcast", "club": None, "name": show, "url": url}
       for slug, show, url in NATIONAL_PODCASTS]
)


def ensure() -> int:
    """Register SOURCES (a re-run refreshes them and keeps their health). Returns how many."""
    db.put_news_sources(SOURCES)
    return len(SOURCES)
