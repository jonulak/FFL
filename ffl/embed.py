"""Text embeddings from a local model (fastembed's ONNX bge-small-en-v1.5): no key, no cost, no network once cached.

The API image bakes the model in (FASTEMBED_CACHE_PATH); a dev machine downloads it on first use. The model loads on
the first call, so a process that never searches or embeds never pays its memory. Tests swap `passages`, `query`
and `tokens`.
"""
from __future__ import annotations

import ctypes
import gc
import os
import sys
import threading

MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384
MAX_TOKENS = 512  # what the model reads of a text; the rest is silently dropped

_model = None
_tokenizer = None
_lock = threading.Lock()


def _load():
    global _model
    with _lock:
        if _model is None:
            from fastembed import TextEmbedding  # onnxruntime is heavy: only imported when something embeds
            _model = TextEmbedding(MODEL, cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"))
    return _model


def unload() -> None:
    """Give the model's memory back (about 350 MB); the next call loads it again. Each news run ends with this, as it
    runs every 10 minutes and a search is rare. A call in progress keeps its own reference and finishes."""
    global _model, _tokenizer
    with _lock:
        _model = _tokenizer = None
    gc.collect()
    if sys.platform == "linux":
        ctypes.CDLL("libc.so.6").malloc_trim(0)  # freed is not returned: glibc holds on to 60 MB of it otherwise


def passages(texts: list[str]) -> list[list[float]]:
    """The stored side: one vector per text."""
    # batch 8, not fastembed's 256: on ~450-token pieces 256 peaked at 5 GB and ran slower (57 vs 34 ms a piece, #112)
    return [v.tolist() for v in _load().passage_embed(texts, batch_size=8)]


def query(text: str) -> list[float]:
    """The searching side (bge prefixes a query differently from a passage)."""
    return next(iter(_load().query_embed(text))).tolist()


def tokens(text: str) -> int:
    """How many tokens the model reads `text` as, its two markers included, never truncated: a piece over MAX_TOKENS
    would lose its end."""
    global _tokenizer
    tok = _tokenizer  # a local: unload() may clear the global from another thread
    if tok is None:
        from tokenizers import Tokenizer  # fastembed's own dependency
        tok = Tokenizer.from_file(str(_load().model._model_dir / "tokenizer.json"))  # its copy truncates at 512
        tok.no_truncation()
        tok.no_padding()
        _tokenizer = tok
    return len(tok.encode(text).ids)
