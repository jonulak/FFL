"""Single-flight: concurrent callers of one key share the call in flight instead of each making it.

The first caller runs `fn`; every caller that arrives while it runs waits and gets the same result, or the same
exception raised again. Nothing is kept once the call lands: the caches around it decide what is reused.
"""
from __future__ import annotations

import threading
from typing import Callable, Hashable, TypeVar

T = TypeVar("T")


class _Call:
    def __init__(self):
        self.done = threading.Event()
        self.result = None
        self.error: BaseException | None = None
        self.waiters = 0


class Flight:
    def __init__(self):
        self._lock = threading.Lock()
        self._calls: dict[Hashable, _Call] = {}

    def do(self, key: Hashable, fn: Callable[[], T]) -> T:
        with self._lock:
            call = self._calls.get(key)
            leader = call is None
            if leader:
                call = self._calls[key] = _Call()
            else:
                call.waiters += 1
        if not leader:
            call.done.wait()
            if call.error is not None:
                raise call.error
            return call.result
        try:
            call.result = fn()
        except BaseException as e:  # noqa: BLE001 — every waiter gets what the caller got, even an interrupt
            call.error = e
            raise
        finally:
            with self._lock:
                del self._calls[key]
            call.done.set()
        return call.result

    def waiting(self, key: Hashable) -> int:
        """Callers parked on `key`'s call in flight (tests)."""
        with self._lock:
            call = self._calls.get(key)
            return call.waiters if call else 0
