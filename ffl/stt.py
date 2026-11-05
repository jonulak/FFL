"""Speech to text for podcast episodes that ship no transcript: NVIDIA Parakeet TDT 0.6B v2 over onnx-asr, on the CPU.

The `stt` image (Dockerfile.stt) copies this file alone beside the model; the news desk reaches it over HTTP
(`python -m ffl.stt`, POST /transcribe, FFL_STT_URL), one episode at a time, and only while Settings › App has
transcription on. The container is always in the stack and cheap when unused: the model (up to 4 GB with an episode
going through it) loads on the first request and is dropped after IDLE_S without one. The contract:

  in   {url}                                          (the episode's audio enclosure)
  out  {ok: true, segs: [{start, text}], seconds, took}  |  {ok: false, error}

`segs` is the shape an Omny transcript or a YouTube caption file is stored in (ffl/newsdesk/media.py), so a
transcribed episode is split and tagged like any other. The top level imports the standard library only: the API
imports this module in its tests without the model's packages.
"""
from __future__ import annotations

import hmac
import json
import os
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "nemo-parakeet-tdt-0.6b-v2"  # English; Dockerfile.stt bakes it in by this name
PORT = 8320
MAX_BYTES = 200 * 1024 * 1024  # an hour-long show is 60 MB
DOWNLOAD_TIMEOUT_S = 120
USER_AGENT = "ffl-stt/1 (+https://github.com/jonulak/FFL)"
CHUNK_S = 300  # audio decoded at a time: a whole 64-minute episode passed 5 GiB; in chunks it fits in 4 GiB
OVERLAP_S = 30  # longer than a segment can be (onnx_asr's VAD stops one at 20 s), so one chunk always hears it whole
IDLE_S = 1800  # the model is dropped after this long without a request (Settings › App may have switched it off)
LOCK = threading.Lock()  # one episode at a time: the model is the whole CPU
_MODEL = None
_USED = [0.0]  # monotonic time of the last request


def load():
    """The model with silero VAD in front (an episode is minutes; the model takes seconds at a time), loaded on
    the first call after a start or an unload."""
    global _MODEL
    _USED[0] = time.monotonic()
    if _MODEL is None:
        import onnx_asr
        _MODEL = onnx_asr.load_model(MODEL, quantization="int8").with_vad(onnx_asr.load_vad("silero"))
        print(f"ffl stt: {MODEL} loaded", flush=True)
    return _MODEL


def unload_idle(now: float | None = None) -> bool:
    """Drop the model after IDLE_S without a request; True when it did."""
    global _MODEL
    with LOCK:
        if _MODEL is None or (now or time.monotonic()) - _USED[0] < IDLE_S:
            return False
        _MODEL = None
    import gc
    gc.collect()
    print("ffl stt: model dropped after idling", flush=True)
    return True


def fetch(url: str, dest) -> int:
    """The audio into `dest` (a file object), streamed; more than MAX_BYTES is refused. Returns the bytes read."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    size = 0
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_S) as r:
        while chunk := r.read(1 << 20):
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError(f"audio over {MAX_BYTES >> 20} MB")
            dest.write(chunk)
    return size


def recognize(model, path: str) -> tuple[list[dict], float]:
    """(segs, seconds) of an audio file, CHUNK_S read at a time: libsndfile reads mp3; m4a it does not. A chunk keeps
    the segments that start before its last OVERLAP_S, which it hears whole, and the next opens where the last one
    kept ended: nothing is heard twice to be matched up afterwards (VAD merges a repeat into the next speech)."""
    import numpy as np
    import soundfile as sf
    segs, at, tail = [], 0.0, np.empty(0, dtype="float32")
    with sf.SoundFile(path) as a:
        rate, size = a.samplerate, CHUNK_S * a.samplerate
        while True:
            block = a.read(size, dtype="float32", always_2d=True).mean(axis=1)
            audio, last = np.concatenate((tail, block)), len(block) < size
            end = at + len(audio) / rate
            resume = max(at, end - OVERLAP_S)
            for r in model.recognize(audio, sample_rate=rate) if len(audio) else ():
                if last or at + float(r.start) < end - OVERLAP_S:
                    segs.append({"start": round(at + float(r.start), 3), "text": " ".join(r.text.split())})
                    resume = max(resume, at + float(r.end))
            if last:
                return [s for s in segs if s["text"]], end
            tail = audio[int((resume - at) * rate):].copy()
            at = resume


def transcribe(url: str) -> dict:
    """{segs, seconds} of the audio at `url`; raises on a download, decode or model failure."""
    with tempfile.NamedTemporaryFile(suffix=".mp3") as f:
        fetch(url, f)
        f.flush()
        with LOCK:
            segs, seconds = recognize(load(), f.name)
    return {"segs": segs, "seconds": round(seconds, 1)}


def call(req: dict) -> dict:
    """The contract's one call; never raises."""
    started = time.monotonic()
    try:
        url = req["url"]
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ValueError("url must be http(s)")
        return {"ok": True, **transcribe(url), "took": round(time.monotonic() - started, 1)}
    except Exception as e:  # noqa: BLE001 — a bad URL, a 404, an unreadable file, a model error: this episode's answer
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


class Handler(BaseHTTPRequestHandler):
    token = ""  # FFL_JOB_TOKEN, set by serve

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):  # the caller gave up on a long episode
            pass

    def do_GET(self):  # noqa: N802
        if self.path != "/health":
            return self._send(404, {"error": "not_found"})
        self._send(200, {"ok": True, "model": MODEL, "loaded": _MODEL is not None})

    def do_POST(self):  # noqa: N802
        if self.path != "/transcribe":
            return self._send(404, {"error": "not_found"})
        if not hmac.compare_digest(self.headers.get("x-ffl-job") or "", self.token):
            return self._send(403, {"error": "forbidden"})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)))
            if not isinstance(req, dict):
                raise ValueError("not an object")
        except ValueError:
            return self._send(400, {"error": "bad_json"})
        self._send(200, call(req))

    def log_message(self, format, *args):  # noqa: A002
        pass


def serve(token: str, port: int = PORT, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    """The server, not yet serving (tests run it in a thread on port 0)."""
    if not token:
        raise SystemExit("FFL_JOB_TOKEN is not set: stt will not serve without its shared secret")
    handler = type("SttHandler", (Handler,), {"token": token})
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    srv = serve(os.environ.get("FFL_JOB_TOKEN", ""), int(os.environ.get("FFL_STT_PORT") or PORT))

    def reaper():
        while True:
            time.sleep(60)
            unload_idle()
    threading.Thread(target=reaper, daemon=True).start()
    print(f"ffl stt ({MODEL}, loaded on the first request) on :{srv.server_address[1]}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
