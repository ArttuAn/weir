"""Clocks.

Every time-dependent component in weir takes a clock object rather than calling
``time.time()`` directly.  The data plane runs on :class:`RealClock`; tests and
the simulator run on :class:`VirtualClock`, which makes a four-hour retry storm
execute in milliseconds and makes every demo reproducible.
"""

from __future__ import annotations

import heapq
import itertools
import time
from collections.abc import Callable


class RealClock:
    """Wall-clock time, for the live data plane."""

    __slots__ = ()

    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class VirtualClock:
    """A deterministic clock with an event queue.

    The simulator advances virtual time only when every agent is blocked, so a
    run is a pure function of its seed: the numbers in ``docs/`` are
    reproducible to the last digit.
    """

    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self._now = float(start)
        self._queue: list[tuple[float, int, Callable[[], None]]] = []
        self._seq = itertools.count()

    def now(self) -> float:
        return self._now

    def at(self, when: float, fn: Callable[[], None]) -> None:
        """Schedule ``fn`` to run at absolute virtual time ``when``."""
        heapq.heappush(self._queue, (max(when, self._now), next(self._seq), fn))

    def after(self, delay: float, fn: Callable[[], None]) -> None:
        self.at(self._now + max(delay, 0.0), fn)

    def pending(self) -> int:
        return len(self._queue)

    def run(self, until: float | None = None) -> None:
        """Drain the event queue, advancing virtual time as it goes."""
        while self._queue:
            when, _, fn = self._queue[0]
            if until is not None and when > until:
                self._now = until
                return
            heapq.heappop(self._queue)
            self._now = when
            fn()
        if until is not None:
            self._now = until
