"""ffl-soc.3: no code path reads ESPN cookies from the environment. Every ESPN call goes out under a user's own row
(ffl/espn_auth.py); ESPN_S2/SWID in an env file decide nothing and reach nothing."""
import re
from pathlib import Path

FFL = Path(__file__).resolve().parent.parent / "ffl"
FORBIDDEN = {
    r"""environ(\.get)?\s*[\(\[]\s*["'](ESPN_S2|SWID|espn_s2|swid)["']""": "a cookie read from the environment",
    r"""getenv\(\s*["'](ESPN_S2|SWID|espn_s2|swid)["']""": "a cookie read from the environment",
    r"\bconfig\.(ESPN_S2|SWID|cookies)\b": "config's cookies",
    r"from \.?config import [^\n]*\b(ESPN_S2|SWID|cookies)\b": "config's cookies",
}


def _hits(text: str) -> list[str]:
    return [what for line in text.splitlines() for pat, what in FORBIDDEN.items() if re.search(pat, line)]


def test_the_patterns_catch_what_they_are_for():
    for bad in ('os.environ.get("ESPN_S2")', "os.environ['SWID']", 'os.getenv("SWID")', "config.SWID",
                "cookies=config.cookies()", "from .config import CDP_PORT, ESPN_S2"):
        assert _hits(bad), bad


def test_no_code_reads_espn_cookies_from_the_environment():
    hits = [f"{p.relative_to(FFL.parent)}: {h}" for p in sorted(FFL.rglob("*.py")) for h in _hits(p.read_text())]
    assert hits == []
