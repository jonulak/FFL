"""Text embeddings from a local model (fastembed's ONNX bge-small-en-v1.5): no key, no cost, no network once cached.

The API image bakes the model in (FASTEMBED_CACHE_PATH); a dev machine downloads it on first use. The model loads on
the first call, so a process that never searches or embeds never pays its memory. Tests swap `passages` and `query`.
"""
from __future__ import annotations

import os
import threading

MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384

_model = None
_lock = threading.Lock()


def _load():
    global _model
    with _lock:
        if _model is None:
            from fastembed import TextEmbedding  # onnxruntime is heavy: only imported when something embeds
            _model = TextEmbedding(MODEL, cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"))
    return _model


def passages(texts: list[str]) -> list[list[float]]:
    """The stored side: one vector per text."""
    return [v.tolist() for v in _load().passage_embed(texts)]


def query(text: str) -> list[float]:
    """The searching side (bge prefixes a query differently from a passage)."""
    return next(iter(_load().query_embed(text))).tolist()


