import os
import sys
import threading
import types
import unittest
from unittest.mock import patch

from app import CounterpointRuntime, create_slack_app, normalize_message


CHANNEL_ID = "C0123456789"


def message_body(
    ts: str,
    user_id: str = "U111",
    *,
    event_id: str | None = None,
    team_id: str = "T111",
) -> dict[str, object]:
    return {
        "team_id": team_id,
        "event_id": event_id or f"Ev{ts}",
        "event": {
            "type": "message",
            "channel": CHANNEL_ID,
            "channel_type": "channel",
            "ts": ts,
            "user": user_id,
            "text": f"message {ts}",
        },
    }


class FakeBoltApp:
    def __init__(self, token: str | None = None):
        self.token = token
        self.events = {}
        self.commands = {}

    def event(self, name):
        def register(handler):
            self.events[name] = handler
            return handler

        return register

    def command(self, name):
        def register(handler):
            self.commands[name] = handler
            return handler

        return register


class NormalizeMessageTests(unittest.TestCase):
    def test_accepts_one_top_level_human_message(self):
        body = message_body("1745444400.123456")
        body["event"]["text"] = "  Ship it.  "

        self.assertEqual(
            normalize_message(body, CHANNEL_ID),
            {
                "ts": "1745444400.123456",
                "user_id": "U111",
                "text": "Ship it.",
            },
        )

    def test_rejects_every_disallowed_message_shape(self):
        cases = {
            "wrong channel": {"channel": "C999"},
            "non-public channel": {"channel_type": "group"},
            "empty text": {"text": " \n "},
            "missing user": {"user": None},
            "bot message": {"bot_id": "B111"},
            "message subtype": {"subtype": "message_changed"},
            "thread reply": {"thread_ts": "1745444399.000001"},
        }

        for label, event_changes in cases.items():
            with self.subTest(label=label):
                body = message_body("1745444400.123456")
                body["event"].update(event_changes)
                self.assertIsNone(normalize_message(body, CHANNEL_ID))


class RuntimeTests(unittest.TestCase):
    def test_deduplicates_outer_event_per_team_and_evicts_oldest_key(self):
        runtime = CounterpointRuntime(lambda messages: None, CHANNEL_ID, start_worker=False)
        first = message_body("1.0", event_id="Ev0")

        self.assertTrue(runtime.handle_message(first))
        self.assertFalse(runtime.handle_message(first))
        self.assertTrue(
            runtime.handle_message(
                message_body("1.1", event_id="Ev0", team_id="T222")
            )
        )
        for index in range(1, 500):
            self.assertTrue(
                runtime.handle_message(
                    message_body(str(index + 1), event_id=f"Ev{index}")
                )
            )

        self.assertTrue(runtime.handle_message(first))
        self.assertFalse(
            runtime.handle_message(message_body("500.0", event_id="Ev499"))
        )

    def test_handler_returns_while_worker_analyzer_is_blocked(self):
        analyzer_entered = threading.Event()
        release_analyzer = threading.Event()

        def blocked_analyzer(messages):
            analyzer_entered.set()
            release_analyzer.wait(2)

        runtime = CounterpointRuntime(blocked_analyzer, CHANNEL_ID)
        self.addCleanup(runtime.stop)
        self.addCleanup(release_analyzer.set)
        for index, user_id in enumerate(("U1", "U2", "U1", "U2"), start=1):
            runtime.handle_message(message_body(f"{index}.0", user_id))
        self.assertTrue(analyzer_entered.wait(1), "analyzer never started")

        handler = threading.Thread(
            target=runtime.handle_message,
            args=(message_body("5.0", "U1"),),
        )
        handler.start()
        handler.join(0.25)
        self.assertFalse(handler.is_alive(), "message handler waited for analyzer")

        release_analyzer.set()
        runtime.drain()

    def test_worker_processes_messages_in_timestamp_order(self):
        calls = []
        runtime = CounterpointRuntime(
            lambda messages: calls.append(messages), CHANNEL_ID, start_worker=False
        )
        self.addCleanup(runtime.stop)
        for ts, user_id in (("3.0", "U1"), ("1.0", "U2"), ("4.0", "U1"), ("2.0", "U2")):
            runtime.handle_message(message_body(ts, user_id))

        runtime.start()
        runtime.drain()

        self.assertEqual(
            [message["ts"] for message in calls[0]],
            ["1.0", "2.0", "3.0", "4.0"],
        )

    def test_window_keeps_only_latest_fifteen_messages(self):
        runtime = CounterpointRuntime(lambda messages: None, CHANNEL_ID, start_worker=False)
        self.addCleanup(runtime.stop)
        for index in range(1, 21):
            runtime.handle_message(
                message_body(f"{index}.0", "U1" if index % 2 else "U2")
            )

        runtime.start()
        runtime.drain()

        self.assertEqual(
            [message["ts"] for message in runtime.window],
            [f"{index}.0" for index in range(6, 21)],
        )

    def test_automatic_analysis_requires_four_messages_and_two_users(self):
        calls = []
        runtime = CounterpointRuntime(
            lambda messages: calls.append(messages), CHANNEL_ID, start_worker=False
        )
        self.addCleanup(runtime.stop)
        for index in range(1, 5):
            runtime.handle_message(message_body(f"{index}.0", "U1"))

        runtime.start()
        runtime.drain()
        self.assertEqual(calls, [])

        runtime.handle_message(message_body("5.0", "U2"))
        runtime.drain()
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0]), 5)


class SlackHandlerTests(unittest.TestCase):
    def make_app(self, runtime):
        slack_bolt = types.ModuleType("slack_bolt")
        slack_bolt.App = FakeBoltApp
        with patch.dict(sys.modules, {"slack_bolt": slack_bolt}), patch.dict(
            os.environ, {"SLACK_BOT_TOKEN": "xoxb-test"}
        ):
            return create_slack_app(runtime)

    def test_registers_handlers_and_dissent_acks_before_enqueue(self):
        runtime = CounterpointRuntime(lambda messages: None, CHANNEL_ID, start_worker=False)
        slack_app = self.make_app(runtime)
        ack_observations = []

        slack_app.commands["/dissent"](
            ack=lambda: ack_observations.append(runtime.work_queue.qsize()),
            body={"channel_id": CHANNEL_ID},
            respond=lambda **response: self.fail(f"unexpected response: {response}"),
        )

        self.assertEqual(set(slack_app.events), {"message", "reaction_added"})
        self.assertEqual(ack_observations, [0])
        self.assertEqual(runtime.work_queue.qsize(), 1)

    def test_dissent_rejects_wrong_channel_ephemerally(self):
        runtime = CounterpointRuntime(lambda messages: None, CHANNEL_ID, start_worker=False)
        slack_app = self.make_app(runtime)
        calls = []

        slack_app.commands["/dissent"](
            ack=lambda: calls.append("ack"),
            body={"channel_id": "C999"},
            respond=lambda **response: calls.append(response),
        )

        self.assertEqual(calls[0], "ack")
        self.assertEqual(calls[1]["response_type"], "ephemeral")
        self.assertEqual(runtime.work_queue.qsize(), 0)


if __name__ == "__main__":
    unittest.main()
