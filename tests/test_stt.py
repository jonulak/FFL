"""The speech-to-text service (`ffl/stt.py`): its contract and its HTTP server, with the model stubbed."""
import ast
import sys
import threading
from pathlib import Path

import httpx
import pytest

from ffl import stt

SEGS = [{"start": 0.5, "text": "Josh Allen was sharp."}, {"start": 9.0, "text": "Agreed."}]


def test_stt_imports_only_the_standard_library_at_the_top():
    """The stt image copies ffl/stt.py alone, and the API's tests import it without onnx-asr or soundfile."""
    tree = ast.parse(Path(stt.__file__).read_text())
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    mods = {(n.module if isinstance(n, ast.ImportFrom) else n.names[0].name).split(".")[0] for n in top}
    assert mods <= set(sys.stdlib_module_names) | {"__future__"}, mods - set(sys.stdlib_module_names)


def test_call_answers_segs_and_never_raises(monkeypatch):
    monkeypatch.setattr(stt, "transcribe", lambda url: {"segs": SEGS, "seconds": 12.0})
    got = stt.call({"url": "https://cdn.test/ep.mp3"})
    assert got["ok"] is True and got["segs"] == SEGS and got["seconds"] == 12.0 and "took" in got

    def boom(url):
        raise OSError("404")
    monkeypatch.setattr(stt, "transcribe", boom)
    assert stt.call({"url": "https://cdn.test/ep.mp3"}) == {"ok": False, "error": "OSError: 404"}
    assert "url must be http" in stt.call({"url": "file:///etc/passwd"})["error"]
    assert "KeyError" in stt.call({})["error"]


def test_fetch_refuses_an_oversized_file(monkeypatch, tmp_path):
    class Big:
        def __init__(self):
            self.left = stt.MAX_BYTES + 1

        def read(self, n):
            n = min(n, self.left)
            self.left -= n
            return b"x" * n

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass
    monkeypatch.setattr(stt.urllib.request, "urlopen", lambda req, timeout: Big())
    with open(tmp_path / "a.mp3", "wb") as f, pytest.raises(ValueError, match="over"):
        stt.fetch("https://cdn.test/ep.mp3", f)


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(stt, "transcribe", lambda url: {"segs": SEGS, "seconds": 12.0})
    srv = stt.serve("job-secret", port=0, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_server_health_secret_and_answer(server):
    assert httpx.get(server + "/health").json() == {"ok": True, "model": stt.MODEL}
    assert httpx.get(server + "/nope").status_code == 404
    assert httpx.post(server + "/transcribe", json={"url": "https://x/ep.mp3"}).status_code == 403
    h = {"x-ffl-job": "job-secret"}
    assert httpx.post(server + "/transcribe", content=b"[1", headers=h).status_code == 400
    assert httpx.post(server + "/transcribe", content=b"[1]", headers=h).json() == {"error": "bad_json"}
    got = httpx.post(server + "/transcribe", json={"url": "https://x/ep.mp3"}, headers=h, timeout=10).json()
    assert got["ok"] is True and got["segs"] == SEGS
    assert httpx.post(server + "/run", json={}, headers=h).status_code == 404


def test_server_will_not_start_without_a_secret():
    with pytest.raises(SystemExit):
        stt.serve("", port=0)
