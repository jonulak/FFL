"""The speech-to-text service (`ffl/stt.py`): its contract and its HTTP server, with the model stubbed."""
import ast
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
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


def test_a_long_episode_reaches_the_model_in_pieces_and_keeps_its_times(monkeypatch):
    """The whole file in memory was what got the container killed: 64 minutes of stereo mp3 is 1.9 GB decoded."""
    sizes, got = _fake_episode(monkeypatch, 700, [(1, 3, "a  start"), (10, 11, "  ")])
    assert sizes == [300, 330, 130]  # no speech near the joins: each chunk opens with OVERLAP_S of the last
    assert got["seconds"] == 700.0
    assert got["segs"] == [{"start": 1, "text": "a start"}]


def test_speech_on_a_join_comes_back_once_and_whole(monkeypatch):
    """A chunk cuts what crosses its edge, as the model would hear it: the fake answers "cut off" for that. The next
    chunk opens where the last kept segment ended, so kept speech is never heard twice."""
    speech = [(5, 8, "first"),
              (262, 275, "starts before the last 30 s, so the first chunk keeps it"),
              (276, 279, "in the last 30 s, left for the next chunk"),
              (295, 305, "over the first chunk's end"),
              (320, 322, "second"),
              (640, 645, "last")]
    sizes, got = _fake_episode(monkeypatch, 700, speech)
    assert got["segs"] == [{"start": s, "text": t} for s, _, t in speech]
    assert sizes == [300, 325, 130]  # the second chunk opens at 275, where the kept segment ended


@pytest.mark.parametrize("seconds, sizes", [(300, [300, 30]), (600, [300, 330, 30]), (100, [100])])
def test_an_episode_ending_on_a_chunk_keeps_its_end(monkeypatch, seconds, sizes):
    got_sizes, got = _fake_episode(monkeypatch, seconds, [(seconds - 5, seconds - 2, "the end")])
    assert got_sizes == sizes and got["seconds"] == seconds
    assert got["segs"] == [{"start": seconds - 5, "text": "the end"}]


def test_an_empty_file_transcribes_to_nothing(monkeypatch):
    sizes, got = _fake_episode(monkeypatch, 0, [])
    assert sizes == [] and got == {"segs": [], "seconds": 0.0}


def test_the_overlap_outlasts_the_longest_segment():
    """A segment longer than OVERLAP_S could be cut in both chunks. onnx_asr's VAD stops one at 20 s by default."""
    assert stt.OVERLAP_S > 20 and stt.OVERLAP_S < stt.CHUNK_S


def _fake_episode(monkeypatch, seconds, speech):
    """transcribe() over a stereo file of `seconds` with a model that hears `speech` [(start, end, text)] only where
    it falls in the chunk, cut at the chunk's edges. Returns (chunk lengths in seconds, the reply)."""
    rate = 10

    class File:
        samplerate = rate
        left = seconds * rate

        def __init__(self, path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n, dtype, always_2d):
            n = min(n, File.left)
            File.left -= n
            return np.zeros((n, 2), dtype=dtype)

    sizes = []

    class Model:
        def recognize(self, audio, sample_rate):
            assert audio.ndim == 1 and sample_rate == rate
            length = len(audio) / rate
            at = seconds - File.left / rate - length  # the chunk's start: what has been read, less what it holds
            sizes.append(length)
            for start, end, text in speech:
                if start < at + length and end > at:
                    cut = start < at or end > at + length
                    yield SimpleNamespace(start=max(start, at) - at, end=min(end, at + length) - at,
                                          text="cut off" if cut else text)

    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(SoundFile=File))
    monkeypatch.setattr(stt, "fetch", lambda url, dest: 0)
    monkeypatch.setattr(stt, "load", Model)
    return sizes, stt.transcribe("https://cdn.test/ep.mp3")


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
    assert httpx.get(server + "/health").json() == {"ok": True, "model": stt.MODEL, "loaded": False}
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


def test_model_is_dropped_after_idling(monkeypatch):
    """The container is always in the stack: the model loads on the first request and leaves after IDLE_S."""
    monkeypatch.setattr(stt, "_MODEL", None)
    monkeypatch.setattr(stt, "_USED", [0.0])
    assert stt.unload_idle() is False  # nothing loaded
    monkeypatch.setattr(stt, "_MODEL", object())
    stt._USED[0] = stt.time.monotonic()
    assert stt.unload_idle() is False and stt._MODEL is not None  # just used
    assert stt.unload_idle(stt.time.monotonic() + stt.IDLE_S + 1) is True and stt._MODEL is None
