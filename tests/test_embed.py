import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ffl import embed
from ffl.newsdesk import embed as stage


def test_the_image_bakes_the_model_the_code_loads():
    """The Dockerfile names the model itself (so a code edit keeps its layer): it must be the one ffl.embed loads, or
    production would download another at its first search, and fail offline."""
    assert f"TextEmbedding('{embed.MODEL}')" in (Path(__file__).parents[1] / "Dockerfile").read_text()


def test_unload_lets_go_of_the_model_and_the_next_call_loads_it_again(monkeypatch):
    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=lambda *a, **k: "model"))
    monkeypatch.setattr(embed, "_model", None)
    monkeypatch.setattr(embed, "_tokenizer", "tokenizer")
    assert embed._load() == "model"
    embed.unload()
    assert embed._model is None and embed._tokenizer is None
    assert embed._load() == "model"


def test_a_news_run_unloads_the_model_even_when_it_fails(monkeypatch):
    unloaded = []
    monkeypatch.setattr(embed, "unload", lambda: unloaded.append(True))

    def boom():
        raise RuntimeError("model failure")

    monkeypatch.setattr(stage, "_stories", boom)
    with pytest.raises(RuntimeError):
        stage.run()
    assert unloaded == [True]
