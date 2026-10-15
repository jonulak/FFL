import threading
import time

import pytest

from ffl.flight import Flight


def park(flight: Flight, key, n: int) -> None:
    """Block until `n` callers wait on `key`'s call in flight: a builder calls it so every racer joins its flight."""
    for _ in range(500):
        if flight.waiting(key) >= n:
            return
        time.sleep(0.01)
    raise AssertionError(f"{n} callers never parked on {key!r}")


def race(n: int, call) -> list:
    """`call()` from n threads at once; each thread's result, or the exception it raised."""
    out: list = [None] * n

    def run(i):
        try:
            out[i] = call()
        except Exception as e:  # noqa: BLE001
            out[i] = e

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    return out


def _gated(n: int, fn, key="k"):
    f = Flight()

    def gated():
        park(f, key, n - 1)
        return fn()

    return race(n, lambda: f.do(key, gated))


def test_concurrent_callers_share_one_call():
    calls = []
    out = _gated(4, lambda: calls.append(1) or {"n": len(calls)})
    assert calls == [1]
    assert out == [{"n": 1}] * 4


def test_an_exception_reaches_every_caller():
    calls = []

    def boom():
        calls.append(1)
        raise ValueError("down")

    out = _gated(3, boom)
    assert calls == [1]
    assert all(isinstance(e, ValueError) and str(e) == "down" for e in out)


def test_a_call_after_the_flight_lands_runs_again():
    f, calls = Flight(), []
    assert f.do("k", lambda: calls.append(1) or 1) == 1
    assert f.do("k", lambda: calls.append(1) or 2) == 2
    assert calls == [1, 1]


def test_a_failed_flight_does_not_stick():
    f = Flight()
    with pytest.raises(ValueError):
        f.do("k", lambda: (_ for _ in ()).throw(ValueError("x")))
    assert f.do("k", lambda: 3) == 3


def test_different_keys_do_not_wait_on_each_other():
    f = Flight()
    inner = []
    assert f.do("a", lambda: inner.append(f.do("b", lambda: 2)) or inner[0]) == 2
