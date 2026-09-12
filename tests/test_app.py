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
        self.client = FakeSlack()

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


def objection(summary="Launch decision"):
    return {
        "action": "object", "confidence": 0.9, "decision_summary": summary,
        "objection": "The rollout lacks a rollback test.",
        "evidence_ids": ["MEM-1"],
        "resolution_question": "Can we test rollback first?", "reason": "Evidence conflicts.",
    }


def reaction_body(ts, user="U9", **changes):
    event = {
        "type": "reaction_added", "reaction": "-1", "user": user,
        "item": {"type": "message", "channel": CHANNEL_ID, "ts": ts},
    }
    event.update(changes)
    return {"event": event}


class FakeSlack:
    def __init__(self):
        self.posts = []
        self.updates = []
        self.post_results = []
        self.update_results = []

    def chat_postMessage(self, **payload):
        self.posts.append(payload)
        result = self.post_results.pop(0) if self.post_results else {"ok": True, "ts": str(100 + len(self.posts))}
        if isinstance(result, Exception):
            raise result
        return result

    def chat_update(self, **payload):
        self.updates.append(payload)
        result = self.update_results.pop(0) if self.update_results else {"ok": True, "ts": payload["ts"]}
        if isinstance(result, Exception):
            raise result
        return result


class FakeTimer:
    created = []

    def __init__(self, interval, callback):
        self.interval = interval
        self.callback = callback
        self.cancelled = False
        self.created.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.callback()


class InterventionTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeSlack()
        self.now = 10.0
        FakeTimer.created = []
        self.timer_patch = patch("app.threading.Timer", FakeTimer)
        self.timer_patch.start()
        self.addCleanup(self.timer_patch.stop)

    def runtime(self, analyzer=None, **kwargs):
        runtime = CounterpointRuntime(analyzer or (lambda messages: objection()), CHANNEL_ID, **kwargs)
        runtime.slack_client = self.client
        self.addCleanup(runtime.stop)
        return runtime

    def feed(self, runtime, start=1, count=4):
        for index in range(start, start + count):
            runtime.handle_message(message_body(str(index), "U1" if index % 2 else "U2"))

    def test_object_posts_once_with_fallback_and_blocks(self):
        runtime = self.runtime(start_worker=False)
        self.feed(runtime)
        runtime.start()
        runtime.drain()
        self.assertEqual(len(self.client.posts), 1)
        post = self.client.posts[0]
        self.assertEqual(post["channel"], CHANNEL_ID)
        for text in ("Counterpoint", "The rollout lacks a rollback test.", "MEM-1", "Can we test rollback first?", "React :thumbsdown: to dismiss and record."):
            self.assertIn(text, post["text"])
            self.assertIn(text, str(post["blocks"]))
        self.assertNotIn("button", str(post["blocks"]))
        self.assertEqual(list(runtime.pending), ["101"])

    def test_abstain_and_malformed_objects_are_silent(self):
        cases = [{"action": "abstain"}, None, objection() | {"action": "other"}]
        for field in ("decision_summary", "objection", "resolution_question"):
            cases.append(objection() | {field: " "})
        for ids in ([], [""], ["a", "b", "c"], "MEM-1", [1]):
            cases.append(objection() | {"evidence_ids": ids})
        for decision in cases:
            with self.subTest(decision=decision):
                runtime = self.runtime(lambda messages: decision)
                self.feed(runtime)
                runtime.drain()
                self.assertEqual(self.client.posts, [])

    def test_post_failure_or_missing_timestamp_leaves_no_pending_or_cooldown(self):
        for result in (RuntimeError("offline"), {"ok": True}, {"ok": True, "ts": ""}):
            with self.subTest(result=result):
                self.client = FakeSlack()
                self.client.post_results = [result]
                runtime = self.runtime()
                self.feed(runtime)
                runtime.drain()
                self.assertEqual(runtime.pending, {})
                self.assertEqual(runtime.cooldown_until, 0)
                self.feed(runtime, 5, 1)
                runtime.drain()
                self.assertEqual(len(runtime.pending), 1)
                self.assertGreater(runtime.cooldown_until, 0)

    def test_stale_result_is_discarded_and_backlog_coalesces_to_newest(self):
        entered, release = threading.Event(), threading.Event()
        snapshots = []

        def analyze(messages):
            snapshots.append(messages)
            if len(snapshots) == 1:
                entered.set()
                release.wait(2)
            return objection()

        runtime = self.runtime(analyze)
        self.addCleanup(release.set)
        self.feed(runtime)
        self.assertTrue(entered.wait(1))
        self.feed(runtime, 5, 4)
        release.set()
        runtime.drain()
        self.assertEqual([len(items) for items in snapshots], [4, 8])
        self.assertEqual(len(self.client.posts), 1)

    def test_generation_rechecked_after_render_immediately_before_publication(self):
        runtime = self.runtime(start_worker=False)
        render = runtime._render_intervention
        rendered = []

        def render_and_accept_message(decision):
            payload = render(decision)
            rendered.append(payload)
            if len(rendered) == 1:
                self.feed(runtime, 5, 1)
            return payload

        runtime._render_intervention = render_and_accept_message
        self.feed(runtime)
        runtime.start()
        runtime.drain()
        self.assertEqual(len(rendered), 2)
        self.assertEqual(len(self.client.posts), 1)

    def test_messages_accepted_during_slack_request_dirty_the_next_window(self):
        entered, release = threading.Event(), threading.Event()
        post = self.client.chat_postMessage
        snapshots = []

        def blocked_post(**payload):
            entered.set()
            release.wait(2)
            return post(**payload)

        self.client.chat_postMessage = blocked_post
        runtime = self.runtime(lambda messages: snapshots.append(messages) or objection())
        self.addCleanup(release.set)
        self.feed(runtime)
        self.assertTrue(entered.wait(1))
        self.feed(runtime, 5, 2)
        release.set()
        runtime.drain()
        self.assertEqual(len(self.client.posts), 1)
        self.assertEqual(len(runtime.window), 6)
        self.assertEqual(len(runtime.pending), 1)
        self.assertEqual(len(snapshots), 1)

    def test_cooldown_updates_window_then_evaluates_once_and_manual_bypasses(self):
        snapshots = []
        runtime = self.runtime(lambda messages: snapshots.append(messages) or objection())
        with patch("app.time.monotonic", side_effect=lambda: self.now):
            self.feed(runtime)
            runtime.drain()
            self.assertEqual(runtime.cooldown_until, 100.0)
            self.feed(runtime, 5, 3)
            runtime.drain()
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(len(runtime.window), 7)
            self.now = 100.0
            FakeTimer.created[-1].fire()
            runtime.drain()
            self.assertEqual(len(snapshots), 2)
            FakeTimer.created[-1].fire()
            runtime.drain()
            self.assertEqual(len(snapshots), 2)
            runtime.request_manual()
            runtime.drain()
            self.assertEqual(len(snapshots), 3)

    def test_manual_requests_keep_each_responder_and_bypass_activity_gate(self):
        responses = [[], []]
        calls = []
        runtime = self.runtime(lambda messages: calls.append(messages) or {"action": "abstain"}, start_worker=False)
        for result in responses:
            runtime.request_manual(lambda result=result, **payload: result.append(payload))
        runtime.start()
        runtime.drain()
        self.assertEqual(calls, [[], []])
        for result in responses:
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["response_type"], "ephemeral")
        self.assertEqual(self.client.posts, [])

    def test_manual_abstention_during_cooldown_keeps_newest_window_evaluation(self):
        snapshots = []
        runtime = self.runtime(lambda messages: snapshots.append(messages) or objection())
        responses = []
        with patch("app.time.monotonic", side_effect=lambda: self.now):
            self.feed(runtime)
            runtime.drain()
            self.feed(runtime, 5, 1)
            runtime.drain()
            runtime.analyzer = lambda messages: snapshots.append(messages) or {"action": "abstain"}
            runtime.request_manual(lambda **payload: responses.append(payload))
            runtime.drain()
            self.assertEqual(len(responses), 1)
            self.now = 100.0
            FakeTimer.created[-1].fire()
            runtime.drain()
            self.assertEqual([len(messages) for messages in snapshots], [4, 5, 5])

    def test_dismissal_rejects_invalid_reactions_and_records_once(self):
        runtime = self.runtime(bot_user_id="UBOT")
        self.feed(runtime)
        runtime.drain()
        for body in (
            {}, reaction_body("101", reaction="thumbsdown"), reaction_body("101", user=""),
            reaction_body("101", user="UBOT"), reaction_body("101", item={"type": "file", "channel": CHANNEL_ID, "ts": "101"}),
            reaction_body("101", item={"type": "message", "channel": "COTHER", "ts": "101"}), reaction_body("unknown"),
        ):
            runtime.handle_reaction(body)
        runtime.drain()
        self.assertEqual(list(runtime.pending), ["101"])
        self.assertEqual(len(self.client.posts), 1)
        runtime.handle_reaction(reaction_body("101"))
        runtime.handle_reaction(reaction_body("101"))
        runtime.drain()
        self.assertEqual(runtime.pending, {})
        self.assertEqual(len(runtime.ledger_rows), 1)
        ledger = next(post for post in self.client.posts if "Decision dissent ledger" in post["text"])
        for text in ("101", "Launch decision", "MEM-1", "<@U9>", "dismissed"):
            self.assertIn(text, ledger["text"])
        replies = [post for post in self.client.posts if post.get("thread_ts") == "101"]
        self.assertEqual([post["text"] for post in replies], ["Understood. Standing down. Logged."])
        runtime.handle_reaction(reaction_body(runtime.ledger_ts))
        runtime.drain()
        self.assertEqual(len(runtime.ledger_rows), 1)
        runtime.analyzer = lambda messages: objection("  LAUNCH   decision  ")
        runtime.request_manual()
        runtime.drain()
        self.assertEqual(len(self.client.posts), 3)

    def test_reaction_queued_during_post_is_applied_after_pending_exists(self):
        entered, release = threading.Event(), threading.Event()
        original_post = self.client.chat_postMessage

        def blocked_post(**payload):
            if len(self.client.posts) == 0:
                entered.set()
                release.wait(2)
            return original_post(**payload)

        self.client.chat_postMessage = blocked_post
        runtime = self.runtime()
        self.addCleanup(release.set)
        self.feed(runtime)
        self.assertTrue(entered.wait(1))
        self.assertTrue(runtime.handle_reaction(reaction_body("101")))
        self.assertEqual(runtime.pending, {})
        release.set()
        runtime.drain()
        self.assertEqual(runtime.pending, {})
        self.assertEqual(len(runtime.ledger_rows), 1)

    def dismiss_next(self, runtime, summary):
        runtime.analyzer = lambda messages: objection(summary)
        runtime.request_manual()
        runtime.drain()
        ts = next(iter(runtime.pending))
        runtime.handle_reaction(reaction_body(ts))
        runtime.drain()
        return ts

    def test_ledger_create_update_and_message_not_found_replacement(self):
        runtime = self.runtime()
        self.dismiss_next(runtime, "First")
        first_ts = runtime.ledger_ts
        self.dismiss_next(runtime, "Second")
        self.assertEqual(runtime.ledger_ts, first_ts)
        self.assertEqual(len(self.client.updates), 1)
        self.client.update_results = [{"ok": False, "error": "message_not_found"}]
        self.dismiss_next(runtime, "Third")
        self.assertNotEqual(runtime.ledger_ts, first_ts)
        self.assertEqual(len(runtime.ledger_rows), 3)
        self.assertIn("First", self.client.posts[-1]["text"])
        self.assertIn("Third", self.client.posts[-1]["text"])
        self.assertFalse(runtime.ledger_dirty)

    def test_ledger_replaces_message_not_found_slack_exception(self):
        runtime = self.runtime()
        self.dismiss_next(runtime, "First")
        old_ts = runtime.ledger_ts
        error = RuntimeError("Slack message_not_found")
        error.response = {"ok": False, "error": "message_not_found"}
        self.client.update_results = [error]
        self.dismiss_next(runtime, "Second")
        self.assertNotEqual(runtime.ledger_ts, old_ts)
        self.assertFalse(runtime.ledger_dirty)

    def test_empty_ledger_create_timestamp_keeps_dirty_state_for_retry(self):
        runtime = self.runtime()
        runtime.request_manual()
        runtime.drain()
        self.client.post_results = [{"ok": True, "ts": "reply"}, {"ok": True, "ts": ""}]
        runtime.handle_reaction(reaction_body("101"))
        runtime.drain()
        self.assertIsNone(runtime.ledger_ts)
        self.assertTrue(runtime.ledger_dirty)
        runtime.handle_reaction(reaction_body("101"))
        runtime.drain()
        self.assertIsNotNone(runtime.ledger_ts)
        self.assertEqual(len(runtime.ledger_rows), 1)
        self.assertFalse(runtime.ledger_dirty)

    def test_invalid_ledger_update_timestamp_keeps_dirty_state_for_retry(self):
        for timestamp in (None, "", " ", 123):
            with self.subTest(timestamp=timestamp):
                self.client = FakeSlack()
                runtime = self.runtime()
                self.dismiss_next(runtime, "First")
                old_ts = runtime.ledger_ts
                self.client.update_results = [{"ok": True, "ts": timestamp}]
                second_ts = self.dismiss_next(runtime, "Second")
                self.assertTrue(runtime.ledger_dirty)
                self.assertEqual(runtime.ledger_ts, old_ts)
                runtime.handle_reaction(reaction_body(second_ts))
                runtime.drain()
                self.assertFalse(runtime.ledger_dirty)
                self.assertEqual(len(runtime.ledger_rows), 2)

    def test_ledger_failures_retain_rows_and_retry_complete_ledger(self):
        for failure in ("create", "update", "replacement"):
            with self.subTest(failure=failure):
                self.client = FakeSlack()
                runtime = self.runtime()
                if failure != "create":
                    self.dismiss_next(runtime, "First")
                old_ts = runtime.ledger_ts
                runtime.analyzer = lambda messages: objection("Retry decision")
                runtime.request_manual()
                runtime.drain()
                intervention_ts = next(iter(runtime.pending))
                if failure == "update":
                    self.client.update_results = [RuntimeError("offline")]
                else:
                    if failure == "replacement":
                        self.client.update_results = [{"ok": False, "error": "message_not_found"}]
                    self.client.post_results = [{"ok": True, "ts": "reply"}, RuntimeError("offline")]
                runtime.handle_reaction(reaction_body(intervention_ts))
                runtime.drain()
                self.assertTrue(runtime.ledger_dirty)
                self.assertEqual(runtime.ledger_ts, old_ts)
                rows = dict(runtime.ledger_rows)
                if failure == "replacement":
                    self.client.update_results = [{"ok": False, "error": "message_not_found"}]
                runtime.handle_reaction(reaction_body(intervention_ts))
                runtime.drain()
                self.assertFalse(runtime.ledger_dirty)
                self.assertEqual(runtime.ledger_rows, rows)
                rendered = (self.client.updates[-1] if failure == "update" else self.client.posts[-1])["text"]
                self.assertIn("Retry decision", rendered)
                if failure != "create":
                    self.assertIn("First", rendered)


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

    def test_running_worker_reorders_late_messages_by_numeric_timestamp(self):
        calls = []
        runtime = CounterpointRuntime(
            lambda messages: calls.append(messages), CHANNEL_ID
        )
        self.addCleanup(runtime.stop)
        for ts, user_id in (
            ("3.0", "U1"),
            ("1.0", "U2"),
            ("10.0", "U1"),
            ("2.0", "U2"),
        ):
            runtime.handle_message(message_body(ts, user_id))
            runtime.drain()

        self.assertEqual(
            [message["ts"] for message in calls[0]],
            ["1.0", "2.0", "3.0", "10.0"],
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

    def test_three_messages_from_two_users_do_not_trigger_analysis(self):
        calls = []
        runtime = CounterpointRuntime(
            lambda messages: calls.append(messages), CHANNEL_ID
        )
        self.addCleanup(runtime.stop)
        for index, user_id in enumerate(("U1", "U2", "U1"), start=1):
            runtime.handle_message(message_body(f"{index}.0", user_id))

        runtime.drain()

        self.assertEqual(calls, [])

    def test_analyzer_exception_does_not_kill_worker_or_block_drain(self):
        attempts = []
        successful_snapshots = []
        first_attempted = threading.Event()

        def analyzer(messages):
            attempts.append(messages)
            if len(attempts) == 1:
                first_attempted.set()
                raise RuntimeError("deliberate analyzer failure")
            successful_snapshots.append(messages)

        runtime = CounterpointRuntime(analyzer, CHANNEL_ID)
        self.addCleanup(runtime.stop)
        for index, user_id in enumerate(("U1", "U2", "U1", "U2"), start=1):
            runtime.handle_message(message_body(f"{index}.0", user_id))
        self.assertTrue(first_attempted.wait(1), "analyzer never raised")

        runtime.handle_message(message_body("5.0", "U1"))
        drain_thread = threading.Thread(target=runtime.drain, daemon=True)
        drain_thread.start()
        drain_thread.join(0.5)

        self.assertFalse(drain_thread.is_alive(), "queue remained unfinished")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(
            [message["ts"] for message in successful_snapshots[0]],
            ["1.0", "2.0", "3.0", "4.0", "5.0"],
        )

    def test_concurrent_submissions_keep_generation_and_enqueue_order_atomic(self):
        snapshots = []
        runtime = CounterpointRuntime(
            lambda messages: snapshots.append(messages), CHANNEL_ID
        )
        self.addCleanup(runtime.stop)
        for index, user_id in enumerate(("U1", "U2", "U1"), start=1):
            runtime.handle_message(message_body(f"{index}.0", user_id))
        runtime.drain()

        first_put_entered = threading.Event()
        release_first_put = threading.Event()
        second_finished = threading.Event()
        original_put = runtime.work_queue.put

        def blocking_put(item):
            if item[0] == "message" and item[1]["ts"] == "4.0":
                first_put_entered.set()
                release_first_put.wait(2)
            original_put(item)

        def submit_second():
            runtime.handle_message(message_body("5.0", "U1"))
            second_finished.set()

        runtime.work_queue.put = blocking_put
        self.addCleanup(release_first_put.set)

        first = threading.Thread(
            target=runtime.handle_message,
            args=(message_body("4.0", "U2"),),
        )
        second = threading.Thread(target=submit_second)
        first.start()
        self.assertTrue(first_put_entered.wait(1), "first enqueue never paused")
        second.start()
        second_finished_early = second_finished.wait(0.25)
        release_first_put.set()
        first.join(1)
        second.join(1)
        runtime.drain()

        self.assertFalse(second_finished_early, "second submit passed first generation")
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(
            [message["ts"] for message in snapshots[0]],
            ["1.0", "2.0", "3.0", "4.0"],
        )


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

    def test_handlers_retain_manual_responder_and_wire_reaction_to_worker(self):
        runtime = CounterpointRuntime(lambda messages: {"action": "abstain"}, CHANNEL_ID)
        self.addCleanup(runtime.stop)
        slack_app = self.make_app(runtime)
        responses = []
        slack_app.commands["/dissent"](
            ack=lambda: None, body={"channel_id": CHANNEL_ID},
            respond=lambda **payload: responses.append(payload),
        )
        runtime.drain()
        self.assertEqual(responses[0]["response_type"], "ephemeral")
        runtime.analyzer = lambda messages: objection()
        runtime.request_manual()
        runtime.drain()
        self.assertEqual(len(slack_app.client.posts), 1)
        slack_app.events["reaction_added"](reaction_body("101"))
        runtime.drain()
        self.assertEqual(len(runtime.ledger_rows), 1)

    def test_app_preserves_explicit_runtime_slack_client(self):
        client = FakeSlack()
        runtime = CounterpointRuntime(lambda messages: objection(), CHANNEL_ID, slack_client=client)
        self.addCleanup(runtime.stop)
        slack_app = self.make_app(runtime)
        runtime.request_manual()
        runtime.drain()
        self.assertEqual(len(client.posts), 1)
        self.assertEqual(slack_app.client.posts, [])


if __name__ == "__main__":
    unittest.main()
