"""scripts/source_check.py's runner: line shapes, order, the exit code. The checks themselves are network calls, never run here."""
import importlib.util
import os
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "source_check.py"


def _load():
    """The script as a module, its PGOPTIONS taken back out: left set, every later test's DB session would be read-only.
    Returns (module, the PGOPTIONS it set)."""
    before = os.environ.pop("PGOPTIONS", None)
    try:
        spec = importlib.util.spec_from_file_location("source_check", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, os.environ.get("PGOPTIONS")
    finally:
        os.environ.pop("PGOPTIONS", None)
        if before is not None:
            os.environ["PGOPTIONS"] = before


sc, SET_PGOPTIONS = _load()


def test_the_script_makes_every_db_session_read_only_before_importing_ffl():
    assert SET_PGOPTIONS == "-c default_transaction_read_only=on"
    src = SCRIPT.read_text()
    assert src.index('os.environ.setdefault("PGOPTIONS"') < src.index("from ffl")


def _boom():
    raise RuntimeError("403 Forbidden\n  for url")


def _exit():
    raise SystemExit("FFC ADP 2qb: bad")


def test_ok_and_fail_lines_in_order():
    res = sc.run([("a", lambda: "3 rows"), ("b", _boom), ("c", _exit), ("d", lambda: None)], workers=2)
    assert [sc.line(r) for r in res] == ["OK a 3 rows", "FAIL b RuntimeError: 403 Forbidden for url",
                                         "FAIL c SystemExit: FFC ADP 2qb: bad", "OK d"]


def test_report_exit_code(capsys):
    assert sc.report([("a", "OK", "x")]) == 0
    assert sc.report([("a", "OK", "x"), ("b", "FAIL", "y")]) == 1
    out = capsys.readouterr().out
    assert "FAIL b y" in out and "-- 1 OK, 1 FAIL, 0 SKIP" in out


def test_disabled_source_is_a_skip_not_a_fail(monkeypatch, capsys):
    monkeypatch.setenv("FFL_SOURCES_OFF", "sharks,youtube_captions")
    res = sc.run([("fantasysharks", sc.sharks), ("youtube-transcript", sc.youtube_transcript), ("a", lambda: "x")])
    assert [sc.line(r) for r in res] == ["SKIP fantasysharks disabled (FFL_SOURCES_OFF=sharks)",
                                         "SKIP youtube-transcript disabled (FFL_SOURCES_OFF=youtube_captions)", "OK a x"]
    assert sc.report(res) == 0
    assert "-- 1 OK, 0 FAIL, 2 SKIP" in capsys.readouterr().out


def test_select_filters_by_substring():
    cs = [("espn-mRoster[x]", None), ("seed-pft", None), ("jev", None)]
    assert [n for n, _ in sc.select(cs, ["seed", "jev"])] == ["seed-pft", "jev"]
    assert sc.select(cs, None) == cs


def test_error_detail_is_capped():
    _, status, detail = sc.run_one("x", lambda: (_ for _ in ()).throw(ValueError("z" * 1000)))
    assert status == "FAIL" and len(detail) == 300
