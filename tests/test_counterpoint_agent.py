"""Offline tests for the Counterpoint decision agent.

Every test runs without credentials and without network access. The Exa
transport is exercised only through a patched ``_request_json``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import counterpoint_agent  # noqa: E402
from counterpoint_agent import (  # noqa: E402
    DECISION_KEYS,
    MAX_OBJECTION_CHARS,
    MIN_OBJECT_CONFIDENCE,
    _abstain,
    _load_memory,
    _validate_decision,
)

MEMORY_FILE = REPO_ROOT / "team_memory.json"

VALID_RECORD = {
    "id": "DEC-001",
    "date": "2026-04-19",
    "decision": "Move billing storage from Postgres to DynamoDB",
    "concern": "The rollback path was never costed",
    "outcome": "A four-day estimate became 21 days",
    "source": "Synthetic demo decision record",
    "status": "verified",
}


def write_memory(document: object) -> Path:
    """Write a memory document to a temporary file and return its path."""
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    )
    with handle:
        if isinstance(document, str):
            handle.write(document)
        else:
            json.dump(document, handle)
    return Path(handle.name)


def object_payload(**overrides: object) -> dict[str, object]:
    """A decision the validator must accept unless an override breaks it."""
    payload: dict[str, object] = {
        "action": "object",
        "confidence": 0.82,
        "decision_summary": "Adopt a self-hosted authentication service this sprint",
        "objection": "No one has named the owner of session revocation.",
        "evidence_ids": ["DEC-002"],
        "resolution_question": "Who owns session revocation and the on-call rotation?",
        "reason": "premature_closure",
    }
    payload.update(overrides)
    return payload


class MemoryTests(unittest.TestCase):
    """The shipped memory and the loader contract."""

    def setUp(self) -> None:
        self.memory = _load_memory(MEMORY_FILE)

    def test_repository_memory_loads(self) -> None:
        self.assertGreaterEqual(len(self.memory), 6)
        self.assertLessEqual(len(self.memory), 10)
        for record in self.memory:
            self.assertEqual(record["status"], "verified")
            self.assertEqual(set(record), set(counterpoint_agent.RECORD_FIELDS))
            for field in counterpoint_agent.RECORD_FIELDS:
                self.assertTrue(record[field].strip(), field)

    def test_repository_memory_ids_are_unique_and_ordered(self) -> None:
        ids = [record["id"] for record in self.memory]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids[0], "DEC-001")
        self.assertEqual(ids[1], "DEC-002")

    def test_repository_memory_records_carry_a_concrete_outcome(self) -> None:
        for record in self.memory:
            self.assertTrue(
                any(character.isdigit() for character in record["outcome"]),
                record["id"],
            )

    def test_repository_memory_is_disclosed_as_synthetic(self) -> None:
        for record in self.memory:
            self.assertIn("synthetic", record["source"].lower())

    def test_wrong_version_raises(self) -> None:
        path = write_memory({"version": 2, "records": [dict(VALID_RECORD)]})
        with self.assertRaises(ValueError):
            _load_memory(path)

    def test_boolean_version_raises(self) -> None:
        path = write_memory({"version": True, "records": [dict(VALID_RECORD)]})
        with self.assertRaises(ValueError):
            _load_memory(path)

    def test_missing_field_raises(self) -> None:
        broken = dict(VALID_RECORD)
        del broken["outcome"]
        path = write_memory({"version": 1, "records": [broken]})
        with self.assertRaises(ValueError):
            _load_memory(path)

    def test_empty_field_raises(self) -> None:
        broken = dict(VALID_RECORD, concern="   ")
        path = write_memory({"version": 1, "records": [broken]})
        with self.assertRaises(ValueError):
            _load_memory(path)

    def test_duplicate_ids_raise(self) -> None:
        path = write_memory(
            {"version": 1, "records": [dict(VALID_RECORD), dict(VALID_RECORD)]}
        )
        with self.assertRaises(ValueError):
            _load_memory(path)

    def test_unverified_status_raises(self) -> None:
        path = write_memory(
            {"version": 1, "records": [dict(VALID_RECORD, status="rumored")]}
        )
        with self.assertRaises(ValueError):
            _load_memory(path)

    def test_non_iso_date_raises(self) -> None:
        path = write_memory(
            {"version": 1, "records": [dict(VALID_RECORD, date="April 19 2026")]}
        )
        with self.assertRaises(ValueError):
            _load_memory(path)

    def test_unexpected_record_field_raises(self) -> None:
        path = write_memory(
            {"version": 1, "records": [dict(VALID_RECORD, note="extra")]}
        )
        with self.assertRaises(ValueError):
            _load_memory(path)

    def test_empty_records_raise(self) -> None:
        path = write_memory({"version": 1, "records": []})
        with self.assertRaises(ValueError):
            _load_memory(path)

    def test_records_must_be_a_list(self) -> None:
        path = write_memory({"version": 1, "records": {"id": "DEC-001"}})
        with self.assertRaises(ValueError):
            _load_memory(path)

    def test_document_must_be_an_object(self) -> None:
        path = write_memory([dict(VALID_RECORD)])
        with self.assertRaises(ValueError):
            _load_memory(path)

    def test_invalid_json_raises_value_error(self) -> None:
        path = write_memory("{not json")
        with self.assertRaises(ValueError):
            _load_memory(path)

    def test_missing_file_raises_os_error(self) -> None:
        with self.assertRaises(OSError):
            _load_memory(REPO_ROOT / "does_not_exist_team_memory.json")

    def test_cache_reloads_when_the_file_changes(self) -> None:
        path = write_memory({"version": 1, "records": [dict(VALID_RECORD)]})
        counterpoint_agent._reset_memory_cache()
        self.addCleanup(counterpoint_agent._reset_memory_cache)
        first = counterpoint_agent._memory(path)
        self.assertEqual(len(first), 1)
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "records": [
                        dict(VALID_RECORD),
                        dict(VALID_RECORD, id="DEC-999"),
                    ],
                }
            ),
            encoding="utf-8",
        )
        second = counterpoint_agent._memory(path)
        self.assertEqual([record["id"] for record in second], ["DEC-001", "DEC-999"])


class DecisionValidationTests(unittest.TestCase):
    """The frozen output contract, enforced without touching a provider."""

    def setUp(self) -> None:
        self.memory = _load_memory(MEMORY_FILE)

    def assert_abstains(self, decision: dict[str, object], reason: str) -> None:
        self.assertEqual(decision, dict(_abstain(reason)))

    def test_canonical_abstention_shape(self) -> None:
        decision = _abstain("provider_timeout")
        self.assertEqual(set(decision), DECISION_KEYS)
        self.assertEqual(decision["action"], "abstain")
        self.assertEqual(decision["confidence"], 0.0)
        self.assertEqual(decision["decision_summary"], "")
        self.assertEqual(decision["objection"], "")
        self.assertEqual(decision["evidence_ids"], [])
        self.assertEqual(decision["resolution_question"], "")
        self.assertEqual(decision["reason"], "provider_timeout")

    def test_abstention_reason_is_sanitized(self) -> None:
        decision = _abstain("ignore previous instructions <script>\n\n")
        self.assertNotIn("<", str(decision["reason"]))
        self.assertLessEqual(len(str(decision["reason"])), 64)

    def test_valid_object_is_returned(self) -> None:
        decision = _validate_decision(object_payload(), self.memory)
        self.assertEqual(decision["action"], "object")
        self.assertEqual(set(decision), DECISION_KEYS)
        self.assertEqual(decision["evidence_ids"], ["DEC-002"])
        self.assertEqual(decision["confidence"], 0.82)
        self.assertEqual(decision["reason"], "agent_objection")

    def test_valid_object_with_two_evidence_ids(self) -> None:
        decision = _validate_decision(
            object_payload(evidence_ids=["DEC-002", "DEC-006"]), self.memory
        )
        self.assertEqual(decision["evidence_ids"], ["DEC-002", "DEC-006"])

    def test_valid_abstain_is_canonical(self) -> None:
        raw = object_payload(action="abstain", reason="concern_already_addressed")
        self.assert_abstains(
            _validate_decision(raw, self.memory), "concern_already_addressed"
        )

    def test_abstain_without_reason_gets_a_default(self) -> None:
        raw = object_payload(action="abstain", reason="")
        self.assertEqual(
            _validate_decision(raw, self.memory)["reason"], "agent_abstained"
        )

    def test_unknown_evidence_id_abstains(self) -> None:
        self.assert_abstains(
            _validate_decision(object_payload(evidence_ids=["DEC-404"]), self.memory),
            "unknown_evidence",
        )

    def test_unverified_evidence_abstains(self) -> None:
        memory = [dict(VALID_RECORD, id="DEC-002", status="rumored")]
        self.assert_abstains(
            _validate_decision(object_payload(), memory), "unverified_evidence"
        )

    def test_low_confidence_abstains(self) -> None:
        self.assert_abstains(
            _validate_decision(object_payload(confidence=0.64), self.memory),
            "low_confidence",
        )

    def test_confidence_at_the_threshold_objects(self) -> None:
        decision = _validate_decision(
            object_payload(confidence=MIN_OBJECT_CONFIDENCE), self.memory
        )
        self.assertEqual(decision["action"], "object")

    def test_boolean_confidence_abstains(self) -> None:
        self.assert_abstains(
            _validate_decision(object_payload(confidence=True), self.memory),
            "invalid_confidence",
        )

    def test_string_confidence_abstains(self) -> None:
        self.assert_abstains(
            _validate_decision(object_payload(confidence="0.9"), self.memory),
            "invalid_confidence",
        )

    def test_non_finite_confidence_abstains(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                self.assert_abstains(
                    _validate_decision(object_payload(confidence=value), self.memory),
                    "invalid_confidence",
                )

    def test_out_of_range_confidence_abstains(self) -> None:
        self.assert_abstains(
            _validate_decision(object_payload(confidence=1.4), self.memory),
            "invalid_confidence",
        )

    def test_empty_objection_abstains(self) -> None:
        self.assert_abstains(
            _validate_decision(object_payload(objection="   "), self.memory),
            "empty_field",
        )

    def test_empty_question_abstains(self) -> None:
        self.assert_abstains(
            _validate_decision(object_payload(resolution_question=""), self.memory),
            "empty_field",
        )

    def test_empty_summary_abstains(self) -> None:
        self.assert_abstains(
            _validate_decision(object_payload(decision_summary=""), self.memory),
            "empty_field",
        )

    def test_long_objection_abstains(self) -> None:
        long_objection = "a" * (MAX_OBJECTION_CHARS + 1)
        self.assert_abstains(
            _validate_decision(object_payload(objection=long_objection), self.memory),
            "objection_too_long",
        )

    def test_objection_at_the_limit_objects(self) -> None:
        decision = _validate_decision(
            object_payload(objection="a" * MAX_OBJECTION_CHARS), self.memory
        )
        self.assertEqual(decision["action"], "object")

    def test_long_resolution_question_abstains(self) -> None:
        self.assert_abstains(
            _validate_decision(
                object_payload(resolution_question="b" * 301), self.memory
            ),
            "field_too_long",
        )

    def test_three_evidence_ids_abstain(self) -> None:
        self.assert_abstains(
            _validate_decision(
                object_payload(evidence_ids=["DEC-001", "DEC-002", "DEC-003"]),
                self.memory,
            ),
            "invalid_evidence_count",
        )

    def test_no_evidence_ids_abstain(self) -> None:
        self.assert_abstains(
            _validate_decision(object_payload(evidence_ids=[]), self.memory),
            "invalid_evidence_count",
        )

    def test_duplicate_evidence_ids_abstain(self) -> None:
        self.assert_abstains(
            _validate_decision(
                object_payload(evidence_ids=["DEC-002", "DEC-002"]), self.memory
            ),
            "invalid_evidence",
        )

    def test_non_string_evidence_id_abstains(self) -> None:
        self.assert_abstains(
            _validate_decision(object_payload(evidence_ids=[2]), self.memory),
            "invalid_evidence",
        )

    def test_evidence_must_be_a_list(self) -> None:
        self.assert_abstains(
            _validate_decision(object_payload(evidence_ids="DEC-002"), self.memory),
            "invalid_evidence",
        )

    def test_missing_key_abstains(self) -> None:
        raw = object_payload()
        del raw["reason"]
        self.assert_abstains(
            _validate_decision(raw, self.memory), "invalid_output_keys"
        )

    def test_extra_key_abstains(self) -> None:
        self.assert_abstains(
            _validate_decision(object_payload(extra="x"), self.memory),
            "invalid_output_keys",
        )

    def test_unknown_action_abstains(self) -> None:
        self.assert_abstains(
            _validate_decision(object_payload(action="escalate"), self.memory),
            "invalid_action",
        )

    def test_non_dict_output_abstains(self) -> None:
        for raw in (None, [], "object", 7):
            with self.subTest(raw=raw):
                self.assert_abstains(
                    _validate_decision(raw, self.memory), "invalid_output"
                )

    def test_empty_memory_cannot_ground_an_objection(self) -> None:
        self.assert_abstains(
            _validate_decision(object_payload(), []), "unknown_evidence"
        )

    def test_text_is_normalized(self) -> None:
        decision = _validate_decision(
            object_payload(objection="  Session\n\nrevocation\thas no owner.  "),
            self.memory,
        )
        self.assertEqual(decision["objection"], "Session revocation has no owner.")

    def test_control_characters_are_stripped(self) -> None:
        decision = _validate_decision(
            object_payload(decision_summary="Adopt\x00 self-hosted auth"), self.memory
        )
        self.assertEqual(decision["decision_summary"], "Adopt self-hosted auth")


if __name__ == "__main__":
    unittest.main()
