import os
import queue
import threading
from collections import deque
from decimal import Decimal
from typing import Callable


NormalizedMessage = dict[str, str]
Analyzer = Callable[[list[NormalizedMessage]], object]


def normalize_message(
    body: dict[str, object], channel_id: str
) -> dict[str, str] | None:
    """Return ts/user_id/text for one accepted top-level human message."""
    event = body.get("event")
    if not isinstance(event, dict):
        return None
    if event.get("type") != "message":
        return None
    if event.get("channel") != channel_id or event.get("channel_type") != "channel":
        return None
    if any(event.get(field) is not None for field in ("bot_id", "subtype", "thread_ts")):
        return None

    text = event.get("text")
    user_id = event.get("user")
    timestamp = event.get("ts")
    if not isinstance(text, str) or not text.strip():
        return None
    if not isinstance(user_id, str) or not user_id:
        return None
    if not isinstance(timestamp, str) or not timestamp:
        return None

    return {"ts": timestamp, "user_id": user_id, "text": text.strip()}


class CounterpointRuntime:
    def __init__(
        self,
        analyzer: Analyzer,
        channel_id: str,
        *,
        start_worker: bool = True,
    ) -> None:
        self.analyzer = analyzer
        self.channel_id = channel_id
        self.work_queue: queue.Queue[tuple[str, NormalizedMessage | None, int]] = (
            queue.Queue()
        )
        self.window: deque[NormalizedMessage] = deque(maxlen=15)
        self._seen_event_order: deque[str] = deque(maxlen=500)
        self._seen_event_keys: set[str] = set()
        self._generation = 0
        self._generation_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._stopped = False
        if start_worker:
            self.start()

    @property
    def generation(self) -> int:
        with self._generation_lock:
            return self._generation

    def start(self) -> None:
        if self._worker is not None:
            return
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def stop(self) -> None:
        worker = self._worker
        if worker is None or self._stopped:
            return
        self._stopped = True
        self.work_queue.put(("stop", None, self.generation))
        worker.join()

    def drain(self) -> None:
        self.work_queue.join()

    def handle_message(self, body: dict[str, object]) -> bool:
        message = normalize_message(body, self.channel_id)
        if message is None:
            return False

        team_id = body.get("team_id")
        event_id = body.get("event_id")
        event_key = (
            f"{team_id}:{event_id}"
            if isinstance(team_id, str)
            and team_id
            and isinstance(event_id, str)
            and event_id
            else None
        )
        with self._generation_lock:
            if event_key is not None and event_key in self._seen_event_keys:
                return False
            if event_key is not None:
                if len(self._seen_event_order) == self._seen_event_order.maxlen:
                    oldest = self._seen_event_order.popleft()
                    self._seen_event_keys.remove(oldest)
                self._seen_event_order.append(event_key)
                self._seen_event_keys.add(event_key)
            self._generation += 1
            generation = self._generation

        self.work_queue.put(("message", message, generation))
        return True

    def request_manual(self) -> None:
        self.work_queue.put(("manual", None, self.generation))

    def _run(self) -> None:
        while True:
            first = self.work_queue.get()
            items = [first]
            while True:
                try:
                    items.append(self.work_queue.get_nowait())
                except queue.Empty:
                    break

            items.sort(key=self._work_sort_key)
            should_stop = False
            for kind, message, _generation in items:
                try:
                    if kind == "stop":
                        should_stop = True
                    elif kind == "message":
                        self.window.append(message)
                        if len(self.window) >= 4 and len(
                            {item["user_id"] for item in self.window}
                        ) >= 2:
                            self.analyzer(list(self.window))
                    elif kind == "manual":
                        self.analyzer(list(self.window))
                finally:
                    self.work_queue.task_done()
            if should_stop:
                return

    @staticmethod
    def _work_sort_key(
        item: tuple[str, NormalizedMessage | None, int]
    ) -> tuple[int, Decimal]:
        kind, message, _generation = item
        if kind == "message":
            return (0, Decimal(message["ts"]))
        if kind == "manual":
            return (1, Decimal(0))
        return (2, Decimal(0))


def create_slack_app(runtime: CounterpointRuntime):
    """Register message, /dissent, and reaction_added listeners."""
    from slack_bolt import App

    slack_app = App(token=os.environ.get("SLACK_BOT_TOKEN"))

    @slack_app.event("message")
    def handle_message(body):
        return runtime.handle_message(body)

    @slack_app.command("/dissent")
    def handle_dissent(ack, body, respond):
        ack()
        if body.get("channel_id") != runtime.channel_id:
            respond(
                text="Counterpoint only runs in its configured channel.",
                response_type="ephemeral",
            )
            return
        runtime.request_manual()

    @slack_app.event("reaction_added")
    def handle_reaction_added(body):
        return None

    return slack_app
