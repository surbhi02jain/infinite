import asyncio
from collections import defaultdict


class EventBus:
    """In-memory pub/sub for live run events (SSE fan-out)."""

    def __init__(self):
        self._subs = defaultdict(set)

    def subscribe(self, run_id: str) -> asyncio.Queue:
        q = asyncio.Queue()
        self._subs[run_id].add(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue):
        self._subs[run_id].discard(q)
        if not self._subs[run_id]:
            self._subs.pop(run_id, None)

    def publish(self, run_id: str, event: dict):
        for q in list(self._subs.get(run_id, [])):
            q.put_nowait(event)


bus = EventBus()
