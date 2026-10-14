"""Small in-process cache for expensive read API responses.

Entries are scoped by an index generation supplied by the caller. Concurrent
requests for the same key are coalesced so only one computes the value.
"""
from __future__ import annotations

from collections import OrderedDict
import threading
from typing import Callable, Hashable


class ReadCache:
    def __init__(self, max_entries: int = 64):
        self.max_entries = max(1, int(max_entries))
        self._values: OrderedDict[Hashable, object] = OrderedDict()
        self._inflight: dict[Hashable, threading.Event] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._values.clear()

    def get_or_compute(self, key: Hashable, compute: Callable[[], object]):
        """Return cached value, coalescing concurrent computation.

        Exceptions are propagated and are never cached.
        """
        while True:
            with self._lock:
                if key in self._values:
                    value = self._values.pop(key)
                    self._values[key] = value
                    return value
                event = self._inflight.get(key)
                if event is None:
                    event = threading.Event()
                    self._inflight[key] = event
                    owner = True
                else:
                    owner = False
            if owner:
                break
            event.wait()

        try:
            value = compute()
        except BaseException:
            with self._lock:
                self._inflight.pop(key, None)
                event.set()
            raise
        with self._lock:
            self._inflight.pop(key, None)
            self._values[key] = value
            self._values.move_to_end(key)
            while len(self._values) > self.max_entries:
                self._values.popitem(last=False)
            event.set()
        return value


read_cache = ReadCache(64)
