"""Counterpoint decision agent.

Plan B owns this module. It turns a short window of ordinary Slack messages
into exactly one validated decision dictionary:

    {
        "action": "abstain",            # or "object"
        "confidence": 0.0,
        "decision_summary": "",
        "objection": "",
        "evidence_ids": [],
        "resolution_question": "",
        "reason": "provider_timeout",
    }

Semantic judgment belongs to the Exa Agent run. This module only supplies the
transcript and the verified synthetic memory, enforces the frozen output
contract, and converts every provider or validation failure into the canonical
abstention. ``analyze_window`` never raises and never invents an objection.
"""

from __future__ import annotations

import datetime
import json
import logging
import math
import os
import re
import threading
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("counterpoint.agent")

# --- Frozen decision contract ------------------------------------------------

DECISION_KEYS: frozenset[str] = frozenset(
    {
        "action",
        "confidence",
        "decision_summary",
        "objection",
        "evidence_ids",
        "resolution_question",
        "reason",
    }
)

MIN_OBJECT_CONFIDENCE = 0.65
MAX_OBJECTION_CHARS = 480
MAX_SUMMARY_CHARS = 300
MAX_QUESTION_CHARS = 300
MIN_EVIDENCE_IDS = 1
MAX_EVIDENCE_IDS = 2

# --- Memory contract ---------------------------------------------------------

MEMORY_VERSION = 1
MEMORY_FILENAME = "team_memory.json"
MEMORY_PATH_ENV = "COUNTERPOINT_MEMORY_PATH"
VERIFIED_STATUS = "verified"
MAX_MEMORY_RECORDS = 50
MAX_MEMORY_BYTES = 262_144

RECORD_FIELDS: tuple[str, ...] = (
    "id",
    "date",
    "decision",
    "concern",
    "outcome",
    "source",
    "status",
)

_RECORD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
_REASON_RE = re.compile(r"[^A-Za-z0-9 _.-]+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"\s+")

_MEMORY_LOCK = threading.Lock()
_MEMORY_CACHE: dict[str, Any] = {}


# --- Canonical abstention ----------------------------------------------------


def _abstain(reason: str) -> dict[str, object]:
    """Return the one abstention shape every failure path must produce."""
    return {
        "action": "abstain",
        "confidence": 0.0,
        "decision_summary": "",
        "objection": "",
        "evidence_ids": [],
        "resolution_question": "",
        "reason": _sanitize_reason(reason) or "abstained",
    }


def _sanitize_reason(value: object) -> str:
    """Reduce a reason to a short log-safe token; model text never escapes."""
    if not isinstance(value, str):
        return ""
    cleaned = _REASON_RE.sub("_", _CONTROL_RE.sub("", value)).strip("_ ").strip()
    return cleaned[:64]


def _clean_text(value: object) -> str:
    """Collapse whitespace and drop control characters, or return ''."""
    if not isinstance(value, str):
        return ""
    return _WHITESPACE_RE.sub(" ", _CONTROL_RE.sub("", value)).strip()


# --- Memory loading ----------------------------------------------------------


def _memory_path() -> Path:
    override = os.environ.get(MEMORY_PATH_ENV, "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / MEMORY_FILENAME


def _load_memory(path: Path) -> list[dict[str, str]]:
    """Load and validate the synthetic decision memory.

    Raises ValueError for any malformed document and OSError when the file is
    unreadable. Every returned record carries all seven fields and the
    ``verified`` status; nothing else may ever ground an objection.
    """
    path = Path(path)
    raw_bytes = path.read_bytes()
    if len(raw_bytes) > MAX_MEMORY_BYTES:
        raise ValueError(f"memory file exceeds {MAX_MEMORY_BYTES} bytes")
    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:  # pragma: no cover - defensive
        raise ValueError("memory file is not valid UTF-8") from exc

    if not isinstance(document, dict):
        raise ValueError("memory document must be a JSON object")
    version = document.get("version")
    if isinstance(version, bool) or version != MEMORY_VERSION:
        raise ValueError(f"memory version must be {MEMORY_VERSION}")
    records = document.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("memory must contain a non-empty records list")
    if len(records) > MAX_MEMORY_RECORDS:
        raise ValueError(f"memory holds more than {MAX_MEMORY_RECORDS} records")

    loaded: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"record {index} must be a JSON object")
        extra = set(record) - set(RECORD_FIELDS)
        if extra:
            raise ValueError(f"record {index} has unexpected fields: {sorted(extra)}")
        clean: dict[str, str] = {}
        for field in RECORD_FIELDS:
            value = record.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"record {index} has a missing or empty {field}")
            clean[field] = _clean_text(value)
        if not _RECORD_ID_RE.match(clean["id"]):
            raise ValueError(f"record {index} has an unusable id")
        if clean["id"] in seen:
            raise ValueError(f"duplicate record id {clean['id']}")
        seen.add(clean["id"])
        try:
            datetime.date.fromisoformat(clean["date"])
        except ValueError as exc:
            raise ValueError(f"record {clean['id']} has a non-ISO date") from exc
        if clean["status"] != VERIFIED_STATUS:
            raise ValueError(f"record {clean['id']} is not {VERIFIED_STATUS}")
        loaded.append(clean)
    return loaded


def _memory(path: Path | None = None) -> list[dict[str, str]]:
    """Return the cached memory, reloading when the file changes on disk."""
    target = Path(path) if path is not None else _memory_path()
    stat = target.stat()
    key = (str(target), stat.st_mtime_ns, stat.st_size)
    with _MEMORY_LOCK:
        if _MEMORY_CACHE.get("key") == key:
            return _MEMORY_CACHE["records"]
    records = _load_memory(target)
    with _MEMORY_LOCK:
        _MEMORY_CACHE["key"] = key
        _MEMORY_CACHE["records"] = records
    return records


def _reset_memory_cache() -> None:
    """Drop the cached memory. Used by tests and by the offline CLI."""
    with _MEMORY_LOCK:
        _MEMORY_CACHE.clear()


def _memory_index(memory: object) -> dict[str, dict[str, str]]:
    """Map record id to record, ignoring anything malformed."""
    index: dict[str, dict[str, str]] = {}
    if not isinstance(memory, list):
        return index
    for record in memory:
        if isinstance(record, dict):
            record_id = record.get("id")
            if isinstance(record_id, str) and record_id:
                index[record_id] = record
    return index


# --- Decision validation -----------------------------------------------------


def _validate_decision(raw: object, memory: list[dict[str, str]]) -> dict[str, object]:
    """Return the frozen decision shape, abstaining on any contract violation.

    This layer may only ever suppress. It never upgrades an agent abstention
    into an objection and never supplies evidence the agent did not cite.
    """
    if not isinstance(raw, dict):
        return _abstain("invalid_output")
    if set(raw) != DECISION_KEYS:
        return _abstain("invalid_output_keys")

    action = raw["action"]
    if action == "abstain":
        return _abstain(_sanitize_reason(raw["reason"]) or "agent_abstained")
    if action != "object":
        return _abstain("invalid_action")

    confidence = raw["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return _abstain("invalid_confidence")
    confidence = float(confidence)
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        return _abstain("invalid_confidence")
    if confidence < MIN_OBJECT_CONFIDENCE:
        return _abstain("low_confidence")

    summary = _clean_text(raw["decision_summary"])
    objection = _clean_text(raw["objection"])
    question = _clean_text(raw["resolution_question"])
    if not summary or not objection or not question:
        return _abstain("empty_field")
    if len(objection) > MAX_OBJECTION_CHARS:
        return _abstain("objection_too_long")
    if len(summary) > MAX_SUMMARY_CHARS or len(question) > MAX_QUESTION_CHARS:
        return _abstain("field_too_long")

    evidence_raw = raw["evidence_ids"]
    if not isinstance(evidence_raw, list):
        return _abstain("invalid_evidence")
    if not MIN_EVIDENCE_IDS <= len(evidence_raw) <= MAX_EVIDENCE_IDS:
        return _abstain("invalid_evidence_count")
    index = _memory_index(memory)
    evidence_ids: list[str] = []
    for candidate in evidence_raw:
        if not isinstance(candidate, str):
            return _abstain("invalid_evidence")
        evidence_id = candidate.strip()
        if not evidence_id or evidence_id in evidence_ids:
            return _abstain("invalid_evidence")
        record = index.get(evidence_id)
        if record is None:
            return _abstain("unknown_evidence")
        if record.get("status") != VERIFIED_STATUS:
            return _abstain("unverified_evidence")
        evidence_ids.append(evidence_id)

    return {
        "action": "object",
        "confidence": confidence,
        "decision_summary": summary,
        "objection": objection,
        "evidence_ids": evidence_ids,
        "resolution_question": question,
        "reason": "agent_objection",
    }
