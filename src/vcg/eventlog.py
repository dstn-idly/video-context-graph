"""In-process event log so the UI can show what the backend is actually doing.

Every external call — TwelveLabs, OpenAI, Neo4j, AWS Bedrock, Twitch, agent
tools — appends a line here, and the app renders it live. That turns "trust me,
it called TwelveLabs" into something an audience can watch happen.

Deliberately dependency-free and defensive: emit() must never raise and never
slow a caller down, because it is wired into the hot path of every integration.
"""
import threading
import time
from collections import deque

MAX_EVENTS = 500

SOURCES = ("twelvelabs", "openai", "neo4j", "aws", "agent", "twitch", "pipeline")

_lock = threading.Lock()
_events: deque = deque(maxlen=MAX_EVENTS)
_seq = 0
_start = time.time()


def emit(source: str, message: str, **detail) -> None:
    """Record one event. Never raises — a logging failure must not break work."""
    global _seq
    try:
        now = time.time()
        with _lock:
            _seq += 1
            _events.append({
                "seq": _seq,
                "ts": time.strftime("%H:%M:%S", time.localtime(now)),
                "epoch": now,
                "source": (source or "pipeline").lower(),
                "message": str(message)[:400],
                "detail": {k: str(v)[:200] for k, v in detail.items()},
                "elapsed_ms": int((now - _start) * 1000),
            })
    except Exception:
        pass


def tail(limit: int = 200) -> list[dict]:
    """Most recent events, oldest first. Safe to call from any thread."""
    try:
        with _lock:
            items = list(_events)
        return items[-max(1, limit):]
    except Exception:
        return []


def counts() -> dict:
    """Events per source — the summary chips above the console."""
    try:
        with _lock:
            items = list(_events)
    except Exception:
        return {}
    out: dict = {}
    for event in items:
        out[event["source"]] = out.get(event["source"], 0) + 1
    return out


def clear() -> None:
    global _seq
    try:
        with _lock:
            _events.clear()
            _seq = 0
    except Exception:
        pass


class step:
    """Context manager that logs start and finish with a real duration.

        with eventlog.step("twelvelabs", "Pegasus analyzing clip"):
            ...
    """

    def __init__(self, source: str, message: str, **detail):
        self.source, self.message, self.detail = source, message, detail

    def __enter__(self):
        self._t0 = time.time()
        emit(self.source, f"▶ {self.message}", **self.detail)
        return self

    def __exit__(self, exc_type, exc, tb):
        took = int((time.time() - self._t0) * 1000)
        if exc_type is None:
            emit(self.source, f"✓ {self.message}", took_ms=took, **self.detail)
        else:
            emit(self.source, f"✗ {self.message}: {exc}", took_ms=took)
        return False  # never swallow the exception
