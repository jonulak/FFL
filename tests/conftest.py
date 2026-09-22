import os

import pytest

# The API's lifespan starts the live-scoring socket; a test run must never open it.
os.environ["FFL_LIVE"] = "0"


@pytest.fixture(autouse=True)
def _calibration_in_tmp(monkeypatch, tmp_path):
    """The pool builder snapshots every build for ffl.calibrate; a test build must not write
    into data/calibration."""
    from ffl import calibrate
    monkeypatch.setattr(calibrate, "CAL_DIR", tmp_path / "calibration")
