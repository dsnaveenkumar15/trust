import threading
from contextlib import contextmanager


class Busy(Exception):
    """All live-model slots are in use."""


class Gate:
    """Caps concurrent Ollama requests so live traffic cannot starve the box the trading apps share."""

    def __init__(self, max_concurrent: int = 2):
        self._sem = threading.BoundedSemaphore(max_concurrent)

    @contextmanager
    def slot(self):
        if not self._sem.acquire(blocking=False):
            raise Busy()
        try:
            yield
        finally:
            self._sem.release()
