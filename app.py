import os
import queue
import threading
import time
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
        slack_client=None,
        bot_user_id: str | None = None,
        cooldown_seconds: int = 90,
    ) -> None:
        self.analyzer = analyzer
        self.channel_id = channel_id
        self.slack_client = slack_client
        self.bot_user_id = bot_user_id
        self.cooldown_seconds = cooldown_seconds
        self.work_queue: queue.Queue[tuple[str, object, int]] = (
            queue.Queue()
        )
        self.window: deque[NormalizedMessage] = deque(maxlen=15)
        self._seen_event_order: deque[str] = deque(maxlen=500)
        self._seen_event_keys: set[str] = set()
        self._generation = 0
        self._ingress_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._stopped = False
        self.pending: dict[str, dict] = {}
        self.suppressed_summaries: set[str] = set()
        self.ledger_rows: dict[str, dict] = {}
        self.ledger_dirty = False
        self.ledger_ts: str | None = None
        self.cooldown_until = 0.0
        self._cooldown_timer: threading.Timer | None = None
        if start_worker:
            self.start()

    @property
    def generation(self) -> int:
        with self._ingress_lock:
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
        with self._ingress_lock:
            self.work_queue.put(("stop", None, self._generation))
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
        with self._ingress_lock:
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

    def request_manual(self, respond=None) -> None:
        with self._ingress_lock:
            self.work_queue.put(("manual", respond, self._generation))

    def handle_reaction(self, body: dict[str, object]) -> bool:
        event = body.get("event")
        if not isinstance(event, dict) or event.get("type") != "reaction_added":
            return False
        item = event.get("item")
        user = event.get("user")
        if (
            event.get("reaction") != "-1"
            or not isinstance(item, dict)
            or item.get("type") != "message"
            or item.get("channel") != self.channel_id
            or not isinstance(item.get("ts"), str)
            or not item["ts"]
            or not isinstance(user, str)
            or not user.strip()
            or user == self.bot_user_id
            or event.get("bot_id") is not None
        ):
            return False
        with self._ingress_lock:
            self.work_queue.put(("reaction", {"ts": item["ts"], "user": user}, self._generation))
        return True

    def _wake_cooldown(self) -> None:
        with self._ingress_lock:
            if not self._stopped:
                self.work_queue.put(("cooldown", None, self._generation))

    @staticmethod
    def _summary_key(summary: str) -> str:
        return " ".join(summary.casefold().split())

    @staticmethod
    def _render_intervention(decision: dict) -> dict:
        parts = [
            "Counterpoint", decision["objection"],
            "Evidence: " + ", ".join(decision["evidence_ids"]),
            decision["resolution_question"],
            "React :thumbsdown: to dismiss and record.",
        ]
        return {
            "text": "\n\n".join(parts),
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": parts[0]}},
                *[{"type": "section", "text": {"type": "plain_text", "text": part}} for part in parts[1:-1]],
                {"type": "context", "elements": [{"type": "plain_text", "text": parts[-1]}]},
            ],
        }

    def _analyze(self, generation: int, respond=None) -> None:
        try:
            decision = self.analyzer(list(self.window))
            valid = (
                isinstance(decision, dict)
                and decision.get("action") == "object"
                and all(isinstance(decision.get(field), str) and decision[field].strip()
                        for field in ("decision_summary", "objection", "resolution_question"))
                and isinstance(decision.get("evidence_ids"), list)
                and 1 <= len(decision["evidence_ids"]) <= 2
                and all(isinstance(value, str) and value.strip() for value in decision["evidence_ids"])
            )
            if not valid or self._summary_key(decision["decision_summary"]) in self.suppressed_summaries:
                if respond is not None:
                    respond(text="No evidence-backed objection to raise.", response_type="ephemeral")
                return
            payload = self._render_intervention(decision)
            with self._ingress_lock:
                if generation != self._generation:
                    return
            # The lock check is the publication boundary; ingress stays live during HTTP.
            # An HTTP timeout can leave an accepted but untracked Slack message.
            response = self.slack_client.chat_postMessage(channel=self.channel_id, **payload)
            timestamp = response.get("ts")
            if response.get("ok", True) is False or not isinstance(timestamp, str) or not timestamp.strip():
                return
            self.pending[timestamp] = dict(decision)
            self.cooldown_until = time.monotonic() + self.cooldown_seconds
            if self._cooldown_timer is not None:
                self._cooldown_timer.cancel()
            self._cooldown_timer = threading.Timer(self.cooldown_seconds, self._wake_cooldown)
            self._cooldown_timer.daemon = True
            self._cooldown_timer.start()
        except Exception:
            # Provider/Slack failures intentionally do not create an intervention.
            pass

    def _dismiss(self, reaction: dict) -> None:
        timestamp = reaction["ts"]
        decision = self.pending.pop(timestamp, None)
        if decision is None:
            return
        self.ledger_rows[timestamp] = {**decision, "dismissed_by": reaction["user"]}
        self.suppressed_summaries.add(self._summary_key(decision["decision_summary"]))
        self.ledger_dirty = True
        try:
            self.slack_client.chat_postMessage(
                channel=self.channel_id, thread_ts=timestamp,
                text="Understood. Standing down. Logged.",
            )
        except Exception:
            pass

    def _sync_ledger(self) -> None:
        if not self.ledger_dirty:
            return
        text = "Decision dissent ledger\n\n" + "\n\n".join(
            f"{timestamp} | {row['decision_summary']} | Evidence: {', '.join(row['evidence_ids'])}"
            f" | <@{row['dismissed_by']}> | dismissed"
            for timestamp, row in self.ledger_rows.items()
        )
        try:
            if self.ledger_ts is not None:
                try:
                    response = self.slack_client.chat_update(channel=self.channel_id, ts=self.ledger_ts, text=text)
                except Exception as error:
                    response = getattr(error, "response", {})
                    if response.get("error") != "message_not_found":
                        return
                if response.get("error") != "message_not_found":
                    timestamp = response.get("ts")
                    if response.get("ok", True) and isinstance(timestamp, str) and timestamp.strip():
                        self.ledger_dirty = False
                    return
            response = self.slack_client.chat_postMessage(channel=self.channel_id, text=text)
            timestamp = response.get("ts")
            if response.get("ok", True) and isinstance(timestamp, str) and timestamp.strip():
                self.ledger_ts = timestamp
                self.ledger_dirty = False
        except Exception:
            # Local rows remain authoritative until a later queue item retries them.
            pass

    def _run(self) -> None:
        window_generation = 0
        automatic_dirty = False
        analyzed = False
        while True:
            kind, message, generation = self.work_queue.get()
            try:
                if kind == "stop":
                    if self._cooldown_timer is not None:
                        self._cooldown_timer.cancel()
                    return

                if kind == "message":
                    messages = [*self.window, message]
                    messages.sort(key=lambda item: Decimal(item["ts"]))
                    self.window.clear()
                    self.window.extend(messages[-15:])
                    window_generation = generation
                    automatic_dirty = True
                elif kind == "reaction":
                    self._dismiss(message)

                self._sync_ledger()
                if kind == "manual":
                    if time.monotonic() >= self.cooldown_until:
                        automatic_dirty = False
                    self._analyze(window_generation, message)
                    analyzed = True
                elif (
                    automatic_dirty
                    and (not analyzed or self.work_queue.empty())
                    and time.monotonic() >= self.cooldown_until
                    and len(self.window) >= 4
                    and len({item["user_id"] for item in self.window}) >= 2
                ):
                    automatic_dirty = False
                    self._analyze(window_generation)
                    analyzed = True
            finally:
                self.work_queue.task_done()


def create_slack_app(runtime: CounterpointRuntime):
    """Register message, /dissent, and reaction_added listeners."""
    from slack_bolt import App

    slack_app = App(token=os.environ.get("SLACK_BOT_TOKEN"))
    if runtime.slack_client is None:
        runtime.slack_client = slack_app.client

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
        runtime.request_manual(respond)

    @slack_app.event("reaction_added")
    def handle_reaction_added(body):
        return runtime.handle_reaction(body)

    return slack_app
