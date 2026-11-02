from pathlib import Path

from ffl import embed


def test_the_image_bakes_the_model_the_code_loads():
    """The Dockerfile names the model itself (so a code edit keeps its layer): it must be the one ffl.embed loads, or
    production would download another at its first search, and fail offline."""
    assert f"TextEmbedding('{embed.MODEL}')" in (Path(__file__).parents[1] / "Dockerfile").read_text()
