"""The decision journal's rules: the kinds, the rationale presets the app offers, and when a move followed the app's
call (so it is recorded without asking). Pure; db.py stores the rows.

CLI, for recording and reviewing decisions from a session (`uv run python -m ffl.decisions <cmd>`):
  add --kind K --title T [--decision D] [--week N] [--rationale k1,k2] [--note TEXT] [--alternatives TEXT]
      [--exit TEXT] [--players 1,2]     record a decision (source "session")
  outcome ID "TEXT"                     record what happened
  rationale ID KEY[,KEY] [--note TEXT]  set the rationale on an open row
  list [--week N] [--open]              one line per row; --open: missing a rationale or an outcome
  show ID                               one row as markdown
  export                                every row, oldest first, as markdown
Errors print to stderr and exit 2."""
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

DRAFT_DAY = "2026-09-07"  # entries on or before it are the draft era, which has no fantasy week
_BENCH = {"BE", "IR"}  # season.BENCH_SLOTS; kept here so this module imports nothing


# The Settings page's two switches: `log` (the app records its moves and asks why), `page` (the Decisions page shows).
DEFAULT_PREFS = {"log": True, "page": True}


def merge_prefs(stored: dict, change: dict) -> dict:
    """The stored switches with a change laid over; unknown keys are dropped, a non-bool refused (ValueError).
    No page, no logging: the page is where a logged decision's reason and outcome are kept up."""
    merged = {**DEFAULT_PREFS, **{k: v for k, v in {**stored, **change}.items() if k in DEFAULT_PREFS}}
    for k, v in merged.items():
        if not isinstance(v, bool):
            raise ValueError(f"{k} must be true or false, got {v!r}")
    return {**merged, "log": merged["log"] and merged["page"]}


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
    if day <= DRAFT_DAY:
        return "draft"
    if "waiver claims" in t:
        return "claims"
    if "flex" in t or " start " in f" {t} ":
        return "lineup"
    if re.search(r"\b(add|adds|drop)\b", t):
        return "free_add"
    return "note"


def parse_journal(text: str, week_of=None) -> list[dict]:
    """journal/DECISIONS.md's entries as decisions rows (the one-time import; db_import inserts them if absent).
    `week_of(day)` gives an in-season entry the fantasy week of its date when its title names none (db_import passes
    one off settings' week1_start); without it, or for a draft-era entry, only a title's "Week N" sets the week."""
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
                       else "alternatives" if label.startswith("Alternatives") else "exit_conditions" if label.startswith("Exit condition")
                       else "outcome" if label.startswith("Outcome") else None)
                if key:
                    f[key] = val
        app = bool(_APP.match(e["title"])) or e["title"] == _APP_CLAIMS
        reasoning = f.get("reasoning")
        if reasoning == "(add by hand)":
            reasoning = None
        seen[e["head"]] = seen.get(e["head"], 0) + 1
        if week := re.search(r"Week (\d+)", e["title"]):
            week = int(week[1])
        elif week_of is not None and e["day"] > DRAFT_DAY:
            week = week_of(e["day"])
        rows.append({"at": e["day"], "week": week, "kind": _kind(e["day"], e["title"]),
                     "source": "import", "title": e["title"], "decision": f.get("decision") or "",
                     "rationale": None if app else ["other"], "rationale_note": None if app else reasoning,
                     "facts": {"calls": reasoning} if app and reasoning else None,
                     "alternatives": f.get("alternatives"), "exit_conditions": f.get("exit_conditions"),
                     "outcome": f.get("outcome"), "body": "\n".join(lines), "import_key": f"{e['head']}#{seen[e['head']]}"})
    return rows


def validate_rationale(kind, keys, note):
    if kind not in PRESETS:
        return f"unknown kind {kind!r}"
    bad = [k for k in keys or [] if k not in preset_keys(kind)]
    if bad:
        return f"{', '.join(bad)}: not a {kind} reason ({', '.join(sorted(preset_keys(kind)))})"
    if "other" in (keys or []) and not (note or "").strip():
        return "Other needs a note"
    return None


def _label(kind, key):
    return dict(PRESETS.get(kind, [])).get(key, key)


def to_markdown(r: dict) -> str:
    """One row as the old journal's entry, for reading and review (`export`)."""
    if r["source"] == "import" and r.get("body"):
        return r["body"] + f"\n\n_(id {r['id']})_\n"
    why = ", ".join(_label(r["kind"], k) for k in r["rationale"] or []) or "(needs a rationale)"
    if r.get("rationale_note"):
        why += f" — {r['rationale_note']}"
    lines = [f"## {r['at'][:10]} — {r['title']}", f"- **Decision:** {r['decision']}", f"- **Rationale:** {why}"]
    for key, label in (("alternatives", "Alternatives"), ("exit_conditions", "Exit conditions"), ("outcome", "Outcome")):
        if r.get(key):
            lines.append(f"- **{label}:** {r[key]}")
    return "\n".join(lines) + f"\n\n_(id {r['id']}, {r['kind']}, {r['source']})_\n"


def _ids(s):
    return [int(x) for x in s.split(",") if x.strip()] if s else None


def main(argv: list[str]) -> int:
    import argparse
    import sys
    from . import db
    p = argparse.ArgumentParser(prog="python -m ffl.decisions")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--kind", required=True, choices=KINDS)
    a.add_argument("--title", required=True)
    a.add_argument("--decision", default="")
    a.add_argument("--week", type=int)
    a.add_argument("--rationale")
    a.add_argument("--note")
    a.add_argument("--alternatives")
    a.add_argument("--exit", dest="exit_conditions")
    a.add_argument("--players")
    o = sub.add_parser("outcome")
    o.add_argument("id", type=int)
    o.add_argument("text")
    ra = sub.add_parser("rationale")
    ra.add_argument("id", type=int)
    ra.add_argument("keys")
    ra.add_argument("--note")
    ls = sub.add_parser("list")
    ls.add_argument("--week", type=int)
    ls.add_argument("--open", action="store_true")
    sh = sub.add_parser("show")
    sh.add_argument("id", type=int)
    sub.add_parser("export")
    args = p.parse_args(argv)

    def fail(msg):
        print(msg, file=sys.stderr)
        return 2

    from . import leagues
    ref = leagues.acting()  # FFL_USER's league (FFL_LEAGUE, else their default)
    if args.cmd == "add":
        keys = args.rationale.split(",") if args.rationale else None
        if err := validate_rationale(args.kind, keys, args.note):
            return fail(err)
        i = db.add_decision({"at": now_iso(), "week": args.week, "kind": args.kind, "source": "session", "title": args.title,
                             "decision": args.decision, "rationale": keys, "rationale_note": args.note,
                             "alternatives": args.alternatives, "exit_conditions": args.exit_conditions,
                             "player_ids": _ids(args.players)}, ref=ref)
        print(f"decision {i}")
        return 0
    if args.cmd == "outcome":
        return 0 if db.update_decision(args.id, {"outcome": args.text, "outcome_at": now_iso()}, ref=ref) else fail(f"no decision {args.id}")
    if args.cmd == "rationale":
        row = db.decision(args.id, ref=ref)
        if row is None:
            return fail(f"no decision {args.id}")
        keys = args.keys.split(",")
        # no --note keeps the note already there (and Other is checked against it)
        note = row["rationale_note"] if args.note is None else args.note
        if err := validate_rationale(row["kind"], keys, note):
            return fail(err)
        db.update_decision(args.id, {"rationale": keys} | ({} if args.note is None else {"rationale_note": args.note}), ref=ref)
        return 0
    rows = db.decisions(ref=ref)
    if args.cmd == "show":
        row = db.decision(args.id, ref=ref)
        return fail(f"no decision {args.id}") if row is None else print(to_markdown(row)) or 0
    if args.cmd == "list":
        for r in rows:
            if (args.week is None or r["week"] == args.week) and (not args.open or r["rationale"] is None or r["outcome"] is None):
                flag = "  [needs rationale]" if r["rationale"] is None else ""
                print(f"{r['id']:>4}  {r['at'][:10]}  {r['kind']:<8}  {r['title']}{flag}")
        return 0
    print("\n".join(to_markdown(r) for r in reversed(rows)))  # export: oldest first, as the file read
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))

