"""The decision journal's rules: the kinds, the rationale presets the app offers, and when a move followed the app's
call (so it is recorded without asking). Pure; db.py stores the rows. T5 adds the CLI (`uv run python -m ffl.decisions`)."""
from __future__ import annotations

import re
from datetime import datetime

KINDS = ("lineup", "sit", "unsit", "claims", "free_add", "trade", "draft", "note")

_LINEUP = [("followed_call", "Followed the call"), ("matchup", "Matchup"), ("injury", "Injury/practice report"),
           ("usage", "Usage trend"), ("weather", "Weather"), ("gut", "Gut"), ("other", "Other")]
_ROSTER = [("best_ros", "Best ROS add"), ("injury_fill", "Injury fill-in"), ("bye_cover", "Bye cover"),
           ("stash", "Handcuff/stash"), ("breakout", "Usage breakout"), ("cut_dead", "Cut dead weight"), ("other", "Other")]
_TRADE = [("need", "Positional need"), ("sell_high", "Sell high"), ("buy_low", "Buy low"), ("schedule", "Bye/schedule"),
          ("other", "Other")]
PRESETS = {"lineup": _LINEUP, "sit": _LINEUP, "unsit": _LINEUP, "claims": _ROSTER, "free_add": _ROSTER, "trade": _TRADE,
           "draft": [("other", "Other")], "note": [("other", "Other")]}

_BENCH = {"BE", "IR"}  # season.BENCH_SLOTS; kept here so this module imports nothing


def preset_keys(kind: str) -> set[str]:
    return {k for k, _ in PRESETS[kind]}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def followed_lineup(items: list[tuple[int, str, str]], cards: list[dict]) -> bool:
    """Every moved player lands where his call puts him: a START in a starting slot, anything else off them."""
    call = {c["player_id"]: c["call"] for c in cards}
    return all((call.get(p) == "START") == (to not in _BENCH) for p, _, to in items)


def followed_sit(card: dict, sat: bool) -> bool:
    """Sitting a player the call starts goes against it; standing one back up, or sitting a bench player, does not."""
    return not sat or (card.get("model_call") or card["call"]) != "START"


_HEAD = re.compile(r"^## (\d{4}-\d{2}-\d{2})[^—\n]*— (.+)$")
_BULLET = re.compile(r"^- \*\*(.+?):\*\* ?(.*)$")
_APP = re.compile(r"^Week \d+ (lineup|sit|unsit):")
_APP_CLAIMS = "Waiver claims (from the app)"


def _kind(day: str, title: str) -> str:
    if m := _APP.match(title):
        return m[1]
    if title == _APP_CLAIMS:
        return "claims"
    t = title.lower()
    if day <= "2026-09-07":
        return "draft"
    if "waiver claims" in t:
        return "claims"
    if "flex" in t or " start " in f" {t} ":
        return "lineup"
    if re.search(r"\b(add|adds|drop)\b", t):
        return "free_add"
    return "note"


def parse_journal(text: str) -> list[dict]:
    """journal/DECISIONS.md's entries as decisions rows (the one-time import; db_import inserts them if absent)."""
    entries, cur = [], None
    for line in text.splitlines():
        if line.startswith("## "):
            cur = None
            if m := _HEAD.match(line):
                cur = {"head": line, "day": m[1], "title": m[2].strip(), "lines": [line]}
                entries.append(cur)
        elif cur is not None:
            cur["lines"].append(line)
    # resubmits of one day's plan: only the last one stood
    entries = [e for i, e in enumerate(entries)
               if not (e["title"] == _APP_CLAIMS and any(f["title"] == _APP_CLAIMS and f["day"] == e["day"] for f in entries[i + 1:]))]
    rows, seen = [], {}
    for e in entries:
        lines = e["lines"]
        while lines and lines[-1].strip() in ("", "---"):
            lines.pop()
        f = {}
        for line in lines[1:]:
            if m := _BULLET.match(line):
                label, val = m[1].strip(), m[2].strip() or None
                key = ("decision" if label == "Decision" else "reasoning" if label == "Reasoning"
                       else "alternatives" if label.startswith("Alternatives") else "exit_conditions" if label == "Exit conditions"
                       else "outcome" if label.startswith("Outcome") else None)
                if key:
                    f[key] = val
        app = bool(_APP.match(e["title"])) or e["title"] == _APP_CLAIMS
        reasoning = f.get("reasoning")
        if reasoning == "(add by hand)":
            reasoning = None
        seen[e["head"]] = seen.get(e["head"], 0) + 1
        week = re.search(r"Week (\d+)", e["title"])
        rows.append({"at": e["day"], "week": int(week[1]) if week else None, "kind": _kind(e["day"], e["title"]),
                     "source": "import", "title": e["title"], "decision": f.get("decision") or "",
                     "rationale": None if app else ["other"], "rationale_note": None if app else reasoning,
                     "facts": {"calls": reasoning} if app and reasoning else None,
                     "alternatives": f.get("alternatives"), "exit_conditions": f.get("exit_conditions"),
                     "outcome": f.get("outcome"), "body": "\n".join(lines), "import_key": f"{e['head']}#{seen[e['head']]}"})
    return rows
