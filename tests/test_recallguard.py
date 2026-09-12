import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import recallguard


ROOT = Path(__file__).parents[1]


class RecallGuardTests(unittest.TestCase):
    def setUp(self):
        self.lessons = recallguard.load_memory(
            ROOT / "examples/project_memory.json"
        )

    def test_memory_requires_complete_lessons(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text('{"version":1,"lessons":[{"id":"bad"}]}')

            with self.assertRaises(ValueError):
                recallguard.load_memory(path)

    def test_demo_ingests_four_providers(self):
        events = recallguard.ingest(ROOT / "examples/multi_agent_events.jsonl")

        self.assertEqual(
            {event["provider"] for event in events},
            {"claude-code", "codex", "gemini-cli", "cursor"},
        )

    def test_diff_parser_excludes_file_headers(self):
        parsed = recallguard.parse_diff(
            recallguard.diff_text(ROOT / "examples/remove_deduplication.diff")
        )

        self.assertEqual(parsed["paths"], ["demo/src/webhook.py"])
        self.assertIn(
            "-    if event_id in processed_event_ids:",
            parsed["removed_lines"],
        )
        self.assertNotIn("--- a/demo/src/webhook.py", parsed["removed_lines"])

    def test_verified_conflict_blocks(self):
        result = recallguard.run_review("harmful", ROOT)

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertEqual(result["findings"][0]["lesson_id"], "PAY-001")
        self.assertEqual(
            set(result["scouts"]),
            {"code", "test", "history"},
        )

    def test_safe_change_passes(self):
        result = recallguard.run_review("safe", ROOT)

        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["findings"], [])

    def test_inferred_lesson_never_blocks(self):
        diff = recallguard.diff_text(
            ROOT / "examples/inferred_external_llm.diff"
        )

        findings = recallguard.analyze(diff, self.lessons)

        self.assertEqual(findings[0]["verdict"], "UNKNOWN")
        self.assertEqual(findings[0]["lesson_id"], "DOC-002")

    def test_adding_protected_token_does_not_warn(self):
        diff = (
            "diff --git a/demo/src/webhook.py b/demo/src/webhook.py\n"
            "+++ b/demo/src/webhook.py\n"
            "+    processed_event_ids.add(event_id)\n"
        )

        findings = recallguard.analyze(diff, self.lessons)

        self.assertEqual(findings, [])

    def test_cli_safe_scenario_prints_pass_json(self):
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "recallguard.py"),
                "check",
                "--scenario",
                "safe",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout)["verdict"], "PASS")

    def test_cli_stdin_blocks_harmful_diff(self):
        harmful = recallguard.diff_text(
            ROOT / "examples/remove_deduplication.diff"
        )

        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "recallguard.py"),
                "check",
                "--diff",
                "-",
            ],
            cwd=ROOT,
            input=harmful,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 3)
        self.assertEqual(json.loads(completed.stdout)["verdict"], "BLOCK")

    def test_cli_ai_flag_without_key_keeps_deterministic_verdict(self):
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "recallguard.py"),
                "check",
                "--scenario",
                "safe",
                "--ai",
            ],
            cwd=ROOT,
            env={"PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
        )

        body = json.loads(completed.stdout)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(body["verdict"], "PASS")
        self.assertEqual(body["ai_status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
