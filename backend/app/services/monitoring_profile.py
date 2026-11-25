"""Bounded in-process benchmark timings; never SQL, parameters or credentials."""
from collections import defaultdict
from contextlib import contextmanager
from threading import Lock, local
from time import perf_counter_ns


class PollProfile:
    """Inclusive concurrent wall time is not summed into end-to-end latency."""
    def __init__(self):
        self._lock = Lock()
        self._local = local()
        self._data = defaultdict(lambda: [0, 0, 0])

    @property
    def phase(self):
        return getattr(self._local, "phase", "coordinator")

    def record(self, name, elapsed_ns):
        with self._lock:
            row = self._data[name]
            row[0] += 1
            row[1] += elapsed_ns
            row[2] = max(row[2], elapsed_ns)

    @contextmanager
    def measure(self, name):
        previous = self.phase
        self._local.phase = name
        start = perf_counter_ns()
        try:
            yield
        finally:
            self.record(name, perf_counter_ns() - start)
            self._local.phase = previous

    def wrap(self, name, fn):
        def measured(*args, **kwargs):
            with self.measure(name):
                return fn(*args, **kwargs)
        return measured

    def reset(self):
        with self._lock:
            self._data.clear()

    def snapshot(self):
        with self._lock:
            return {
                k: {"calls": v[0], "total_ms": round(v[1] / 1e6, 3),
                    "max_ms": round(v[2] / 1e6, 3)}
                for k, v in sorted(self._data.items())
            }
