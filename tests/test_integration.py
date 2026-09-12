"""End-to-end tests across both lanes.

The Slack transport and the Exa transport are the only fakes. A real
CounterpointRuntime drives the real analyze_window, so these tests fail if the
frozen contract between Plan A and Plan B drifts.
"""

from __future__ import annotations

import json
import logging
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import app  # noqa: E402
import counterpoint_agent  # noqa: E402
from app import CounterpointRuntime  # noqa: E402

CHANNEL_ID = "C0123456789"
BOT_USER_ID = "U0BOT"

_QUIET_HANDLER = logging.NullHandler()


def setUpModule() -> None:
    counterpoint_agent.LOGGER.addHandler(_QUIET_HANDLER)
    counterpoint_agent.LOGGER.propagate = False


def tearDownModule() -> None:
    counterpoint_agent.LOGGER.removeHandler(_QUIET_HANDLER)
    counterpoint_agent.LOGGER.propagate = True


class FakeSlackClient:
    """Records posts and updates the way the Slack Web API would answer."""

    def __init__(self) -> None:
        self.posts: list[dict[str, object]] = []
        self.updates: list[dict[str, object]] = []
        self._next = 0

    def chat_postMessage(self, **kwargs: object) -> dict[str, object]:
        self._next += 1
        timestamp = f"9000000000.{self._next:06d}"
        self.posts.append({**kwargs, "ts": timestamp})
        return {"ok": True, "ts": timestamp}

    def chat_update(self, **kwargs: object) -> dict[str, object]:
        self.updates.append(dict(kwargs))
        return {"ok": True, "ts": kwargs.get("ts")}

    def top_level_posts(self) -> list[dict[str, object]]:
        return [post for post in self.posts if "thread_ts" not in post]


def message_body(message: dict[str, str], index: int) -> dict[str, object]:
    return {
        "team_id": "T111",
        "event_id": f"Ev{index:04d}",
        "event": {
            "type": "message",
            "channel": CHANNEL_ID,
            "channel_type": "channel",
            "user": message["user_id"],
            "ts": message["ts"],
            "text": message["text"],
        },
    }


def reaction_body(timestamp: str, user: str = "U01ALEXA") -> dict[str, object]:
    return {
        "team_id": "T111",
        "event_id": f"EvR{timestamp}",
        "event": {
            "type": "reaction_added",
            "reaction": "-1",
            "user": user,
            "item": {"type": "message", "channel": CHANNEL_ID, "ts": timestamp},
        },
    }


def completed_run(**overrides: object) -> dict[str, object]:
    output = {
        "action": "object",
        "confidence": 0.88,
        "decision_summary": "Replace the identity vendor with a self-hosted auth service",
        "objection": "Nobody has named an owner for session revocation or the on-call load it adds.",
        "evidence_ids": ["DEC-002"],
        "resolution_question": "Who owns session revocation and the pager rotation?",
        "reason": "premature_closure",
    }
    output.update(overrides)
    return {"id": "run_integration", "status": "completed", "output": output}


def abstaining_run(reason: str = "healthy_agreement") -> dict[str, object]:
    return {
        "id": "run_integration",
        "status": "completed",
        "output": {
            "action": "abstain",
            "confidence": 0.0,
            "decision_summary": "",
            "objection": "",
            "evidence_ids": [],
            "resolution_question": "",
            "reason": reason,
        },
    }


class IntegrationTestCase(unittest.TestCase):
    """One real runtime wired to the real agent over two fake transports."""

    def setUp(self) -> None:
        self.scenarios = counterpoint_agent._load_demo_transcripts()
        self.slack = FakeSlackClient()
        self.exa_calls: list[dict[str, object]] = []
        patcher = mock.patch.dict(
            "os.environ", {"EXA_API_KEY": "integration-key"}, clear=False
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.runtime = CounterpointRuntime(
            analyzer=counterpoint_agent.analyze_window,
            channel_id=CHANNEL_ID,
            slack_client=self.slack,
            bot_user_id=BOT_USER_ID,
            cooldown_seconds=90,
        )
        self.addCleanup(self.runtime.stop)

    def patch_exa(self, responder) -> None:
        def recorded(method, url, **kwargs):
            self.exa_calls.append({"method": method, "url": url, **kwargs})
            return responder(method, url, **kwargs)

        patcher = mock.patch.object(counterpoint_agent, "_request_json", recorded)
        patcher.start()
        self.addCleanup(patcher.stop)

    def always(self, document: object) -> None:
        def responder(method, url, **kwargs):
            if isinstance(document, Exception):
                raise document
            return document

        self.patch_exa(responder)

    def play(self, name: str) -> None:
        for index, message in enumerate(self.scenarios[name]["messages"]):
            self.runtime.handle_message(message_body(message, index))
        self.runtime.drain()


class PrematureClosureTests(IntegrationTestCase):
    """Acceptance scenario 1, 7 and 8 across the full path."""

    def test_one_grounded_objection_is_posted(self) -> None:
        self.always(completed_run())
        self.play("premature_auth_consensus")
        posts = self.slack.top_level_posts()
        self.assertEqual(len(posts), 1)
        text = str(posts[0]["text"])
        self.assertIn("Counterpoint", text)
        self.assertIn("DEC-002", text)
        self.assertIn("React :thumbsdown: to dismiss and record.", text)
        self.assertIn("session revocation", text.lower())
        self.assertTrue(posts[0]["blocks"])

    def test_the_transcript_and_memory_reach_exa_once(self) -> None:
        self.always(completed_run())
        self.play("premature_auth_consensus")
        creates = [call for call in self.exa_calls if call["method"] == "POST"]
        # Six messages coalesce into one evaluated window, so one paid run.
        self.assertEqual(len(creates), 1)
        payload = creates[0]["payload"]
        serialized = json.dumps(payload)
        for message in self.scenarios["premature_auth_consensus"]["messages"]:
            self.assertIn(message["text"], serialized)
        self.assertIn("DEC-002", serialized)
        self.assertEqual(payload["effort"], "minimal")
        self.assertNotIn("budget", payload)
        self.assertNotIn("input", payload)
        self.assertIn("query", payload)

    def test_thumbs_down_stands_down_and_writes_one_ledger_row(self) -> None:
        self.always(completed_run())
        self.play("premature_auth_consensus")
        timestamp = str(self.slack.top_level_posts()[0]["ts"])

        self.runtime.handle_reaction(reaction_body(timestamp))
        self.runtime.drain()

        acknowledgements = [
            post for post in self.slack.posts if post.get("thread_ts") == timestamp
        ]
        self.assertEqual(len(acknowledgements), 1)
        self.assertEqual(
            acknowledgements[0]["text"], "Understood. Standing down. Logged."
        )
        ledgers = [
            post
            for post in self.slack.top_level_posts()
            if str(post["text"]).startswith("Decision dissent ledger")
        ]
        self.assertEqual(len(ledgers), 1)
        self.assertIn("DEC-002", str(ledgers[0]["text"]))
        self.assertIn("dismissed", str(ledgers[0]["text"]))

    def test_a_duplicate_thumbs_down_changes_nothing(self) -> None:
        self.always(completed_run())
        self.play("premature_auth_consensus")
        timestamp = str(self.slack.top_level_posts()[0]["ts"])
        for _ in range(3):
            self.runtime.handle_reaction(reaction_body(timestamp))
        self.runtime.drain()
        acknowledgements = [
            post for post in self.slack.posts if post.get("thread_ts") == timestamp
        ]
        self.assertEqual(len(acknowledgements), 1)

    def test_a_bot_thumbs_down_is_ignored(self) -> None:
        self.always(completed_run())
        self.play("premature_auth_consensus")
        timestamp = str(self.slack.top_level_posts()[0]["ts"])
        self.assertFalse(
            self.runtime.handle_reaction(reaction_body(timestamp, user=BOT_USER_ID))
        )
        self.runtime.drain()
        self.assertEqual(
            [post for post in self.slack.posts if post.get("thread_ts")], []
        )


class SilenceTests(IntegrationTestCase):
    """Acceptance scenarios 2, 4 and 5: Slack stays quiet."""

    def test_healthy_agreement_is_silent(self) -> None:
        self.always(abstaining_run("concern_addressed"))
        self.play("healthy_auth_decision")
        self.assertEqual(self.slack.posts, [])

    def test_irrelevant_memory_is_silent(self) -> None:
        self.always(abstaining_run("no_relevant_precedent"))
        self.play("irrelevant_memory")
        self.assertEqual(self.slack.posts, [])

    def test_invented_evidence_is_silent(self) -> None:
        self.always(completed_run(evidence_ids=["DEC-NOT-REAL"]))
        self.play("premature_auth_consensus")
        self.assertEqual(self.slack.posts, [])

    def test_low_confidence_is_silent(self) -> None:
        self.always(completed_run(confidence=0.4))
        self.play("premature_auth_consensus")
        self.assertEqual(self.slack.posts, [])

    def test_provider_failure_is_silent_and_the_runtime_survives(self) -> None:
        self.always(counterpoint_agent.ProviderError("provider_http_error", 500))
        self.play("premature_auth_consensus")
        self.assertEqual(self.slack.posts, [])
        self.assertTrue(self.runtime._worker.is_alive())

    def test_a_failed_run_is_silent(self) -> None:
        self.always({"id": "run_x", "status": "failed"})
        self.play("premature_auth_consensus")
        self.assertEqual(self.slack.posts, [])

    def test_a_broadcast_ping_is_never_published(self) -> None:
        self.always(
            completed_run(objection="<!channel> stop and rethink this migration.")
        )
        self.play("premature_auth_consensus")
        self.assertEqual(self.slack.posts, [])


class StaleResultTests(IntegrationTestCase):
    """Acceptance scenario 6: the conversation moves on mid-run."""

    LATE_MESSAGE = {
        "ts": "1776067560.000700",
        "user_id": "U03CHENG",
        "text": "Wait, I already own revocation and the pager rotation.",
    }

    def test_a_result_from_a_superseded_window_is_never_published(self) -> None:
        """A message landing mid-run invalidates that run, not the next one."""
        arrived: list[bool] = []

        def responder(method, url, **kwargs):
            if method == "POST" and not arrived:
                arrived.append(True)
                self.runtime.handle_message(message_body(self.LATE_MESSAGE, 99))
            return completed_run()

        self.patch_exa(responder)
        for index, message in enumerate(
            self.scenarios["premature_auth_consensus"]["messages"][:4]
        ):
            self.runtime.handle_message(message_body(message, index))
        self.runtime.drain()

        posts = self.slack.top_level_posts()
        self.assertEqual(len(posts), 1)
        creates = [call for call in self.exa_calls if call["method"] == "POST"]
        self.assertEqual(len(creates), 2)
        # The first run was discarded; only the window containing the late
        # message was allowed to publish.
        self.assertNotIn(self.LATE_MESSAGE["text"], json.dumps(creates[0]["payload"]))
        self.assertIn(self.LATE_MESSAGE["text"], json.dumps(creates[1]["payload"]))

    def test_a_run_that_is_stale_before_it_starts_is_never_paid_for(self) -> None:
        self.always(completed_run())
        for index, message in enumerate(
            self.scenarios["premature_auth_consensus"]["messages"]
        ):
            self.runtime.handle_message(message_body(message, index))
        self.runtime.drain()
        creates = [call for call in self.exa_calls if call["method"] == "POST"]
        self.assertEqual(len(creates), 1)
        self.assertEqual(len(self.slack.top_level_posts()), 1)


class ManualDissentTests(IntegrationTestCase):
    """Acceptance scenario 8: /dissent skips the gate, not the evidence rules."""

    def test_dissent_objects_on_demand(self) -> None:
        self.always(completed_run())
        self.runtime.handle_message(
            message_body(
                self.scenarios["premature_auth_consensus"]["messages"][0], 0
            )
        )
        self.runtime.drain()
        self.assertEqual(self.slack.posts, [])

        replies: list[dict[str, object]] = []
        self.runtime.request_manual(lambda **kwargs: replies.append(kwargs))
        self.runtime.drain()
        self.assertEqual(len(self.slack.top_level_posts()), 1)
        self.assertEqual(replies, [])

    def test_dissent_reports_an_abstention_ephemerally(self) -> None:
        self.always(abstaining_run("no_decision"))
        self.runtime.handle_message(
            message_body(
                self.scenarios["premature_auth_consensus"]["messages"][0], 0
            )
        )
        self.runtime.drain()

        replies: list[dict[str, object]] = []
        self.runtime.request_manual(lambda **kwargs: replies.append(kwargs))
        self.runtime.drain()
        self.assertEqual(self.slack.posts, [])
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["response_type"], "ephemeral")

    def test_dissent_cannot_bypass_evidence_validation(self) -> None:
        self.always(completed_run(evidence_ids=["DEC-000"]))
        self.runtime.handle_message(
            message_body(
                self.scenarios["premature_auth_consensus"]["messages"][0], 0
            )
        )
        self.runtime.drain()
        replies: list[dict[str, object]] = []
        self.runtime.request_manual(lambda **kwargs: replies.append(kwargs))
        self.runtime.drain()
        self.assertEqual(self.slack.posts, [])


class CooldownTests(IntegrationTestCase):
    """A second decision inside the cooldown window stays silent."""

    def test_only_one_post_per_cooldown(self) -> None:
        self.always(completed_run())
        self.play("premature_auth_consensus")
        self.assertEqual(len(self.slack.top_level_posts()), 1)
        for index, message in enumerate(
            self.scenarios["premature_auth_consensus"]["messages"], start=50
        ):
            shifted = dict(message, ts=f"17761{index:05d}.000000")
            self.runtime.handle_message(message_body(shifted, index))
        self.runtime.drain()
        self.assertEqual(len(self.slack.top_level_posts()), 1)


class ContractDriftTests(unittest.TestCase):
    """Guards on the frozen boundary itself."""

    def test_app_consumes_only_the_frozen_keys(self) -> None:
        source = (REPO_ROOT / "app.py").read_text(encoding="utf-8")
        for key in counterpoint_agent.DECISION_KEYS:
            if key in ("action", "reason", "confidence"):
                continue
            self.assertIn(f'"{key}"', source, key)

    def test_the_analyzer_signature_matches_the_contract(self) -> None:
        import inspect

        signature = inspect.signature(counterpoint_agent.analyze_window)
        self.assertEqual(list(signature.parameters), ["messages"])

    def test_production_startup_imports_the_agent(self) -> None:
        source = (REPO_ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("from counterpoint_agent import analyze_window", source)
        self.assertIn("load_dotenv", source)
        self.assertIn("EXA_API_KEY", source)

    def test_the_agent_imports_no_slack_code(self) -> None:
        import ast

        source = (REPO_ROOT / "counterpoint_agent.py").read_text(encoding="utf-8")
        imported: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("slack_bolt", imported)
        self.assertNotIn("slack_sdk", imported)
        self.assertNotIn("app", imported)

    def test_the_agent_calls_no_slack_api(self) -> None:
        source = (REPO_ROOT / "counterpoint_agent.py").read_text(encoding="utf-8")
        for token in ("chat_postMessage", "chat_update", "SocketMode", "slack.com"):
            self.assertNotIn(token, source, token)


if __name__ == "__main__":
    unittest.main()
