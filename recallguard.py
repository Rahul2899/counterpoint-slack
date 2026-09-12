import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


REQUIRED_LESSON_KEYS = {
    "id",
    "claim",
    "status",
    "confidence",
    "paths",
    "block_if_removed",
    "warn_if_changed",
    "evidence",
}
REQUIRED_EVENT_KEYS = {
    "agent_id",
    "provider",
    "task",
    "branch",
    "diff_path",
    "changed_paths",
    "status",
}
VALID_STATUS = {"active", "superseded"}
VALID_CONFIDENCE = {"verified", "inferred", "unknown"}
SCENARIO_DIFF = {
    "harmful": "examples/remove_deduplication.diff",
    "inferred": "examples/inferred_external_llm.diff",
    "safe": "examples/safe_change.diff",
}


def load_memory(path: Path) -> list[dict]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid project memory: {error}") from error

    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("project memory version must be 1")
    lessons = payload.get("lessons")
    if not isinstance(lessons, list):
        raise ValueError("project memory lessons must be a list")

    for lesson in lessons:
        if not isinstance(lesson, dict) or not REQUIRED_LESSON_KEYS <= lesson.keys():
            raise ValueError("project memory contains an incomplete lesson")
        if lesson["status"] not in VALID_STATUS:
            raise ValueError("lesson status must be active or superseded")
        if lesson["confidence"] not in VALID_CONFIDENCE:
            raise ValueError("lesson confidence is invalid")
        for key in ("paths", "block_if_removed", "warn_if_changed", "evidence"):
            if not isinstance(lesson[key], list):
                raise ValueError(f"lesson {key} must be a list")

    return lessons


def ingest(path: Path) -> list[dict]:
    events = []
    try:
        lines = path.read_text().splitlines()
        for line in lines:
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict) or not REQUIRED_EVENT_KEYS <= event.keys():
                raise ValueError("agent event is incomplete")
            events.append(event)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid agent events: {error}") from error
    return events


def diff_text(path: Path) -> str:
    try:
        return path.read_text()
    except OSError as error:
        raise ValueError(f"invalid diff: {error}") from error


def parse_diff(text: str) -> dict[str, list[str]]:
    paths = []
    removed_lines = []
    added_lines = []
    for line in text.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            if path not in paths:
                paths.append(path)
        elif line.startswith("-") and not line.startswith("---"):
            removed_lines.append(line)
        elif line.startswith("+") and not line.startswith("+++"):
            added_lines.append(line)
    return {
        "paths": paths,
        "removed_lines": removed_lines,
        "added_lines": added_lines,
    }


def _matching_lessons(
    parsed: dict[str, list[str]], lessons: list[dict]
) -> list[dict]:
    matches = []
    for lesson in lessons:
        if lesson["status"] != "active":
            continue
        if not set(parsed["paths"]) & set(lesson["paths"]):
            continue
        removed_protection = any(
            token in line
            for token in lesson["block_if_removed"]
            for line in parsed["removed_lines"]
        )
        changed_warning = any(
            token in line
            for token in lesson["warn_if_changed"]
            for line in parsed["removed_lines"] + parsed["added_lines"]
        )
        if removed_protection or changed_warning:
            matches.append(lesson)
    return matches


def analyze(diff: str, lessons: list[dict]) -> list[dict]:
    parsed = parse_diff(diff)
    findings = []
    for lesson in _matching_lessons(parsed, lessons):
        removed = [
            line
            for line in parsed["removed_lines"]
            if any(token in line for token in lesson["block_if_removed"])
        ]
        evidence_kinds = {
            item.get("kind")
            for item in lesson["evidence"]
            if isinstance(item, dict)
        }
        can_block = (
            lesson["confidence"] == "verified"
            and removed
            and {"incident", "test"} <= evidence_kinds
        )
        verdict = "BLOCK" if can_block else "UNKNOWN"
        reason = (
            "Webhook deduplication protected by incident and test evidence "
            "was removed."
            if can_block
            else "Relevant project history is not verified enough to block."
        )
        findings.append(
            {
                "lesson_id": lesson["id"],
                "confidence": lesson["confidence"],
                "status": lesson["status"],
                "verdict": verdict,
                "reason": reason,
                "removed_lines": removed,
                "evidence_refs": [
                    item["ref"]
                    for item in lesson["evidence"]
                    if isinstance(item, dict) and "ref" in item
                ],
            }
        )
    return findings


def _code_scout(parsed: dict[str, list[str]], lessons: list[dict]) -> dict:
    protected = [
        line
        for lesson in _matching_lessons(parsed, lessons)
        for line in parsed["removed_lines"]
        if any(token in line for token in lesson["block_if_removed"])
    ]
    return {
        "status": "complete",
        "summary": (
            "Protected project-memory tokens were removed."
            if protected
            else "No protected tokens were removed."
        ),
        "evidence": protected,
    }


def _evidence_scout(
    name: str,
    kind: str,
    parsed: dict[str, list[str]],
    lessons: list[dict],
) -> tuple[str, dict]:
    evidence = [
        item["ref"]
        for lesson in _matching_lessons(parsed, lessons)
        for item in lesson["evidence"]
        if isinstance(item, dict) and item.get("kind") == kind
    ]
    summaries = {
        "test": "Linked regression-test evidence was found.",
        "history": "Matching active project history was found.",
    }
    return name, {
        "status": "complete",
        "summary": summaries[name] if evidence else f"No {name} evidence matched.",
        "evidence": evidence,
    }


def review_diff(diff: str, events: list[dict], lessons: list[dict]) -> dict:
    parsed = parse_diff(diff)
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [
            pool.submit(_code_scout, parsed, lessons),
            pool.submit(_evidence_scout, "test", "test", parsed, lessons),
            pool.submit(
                _evidence_scout,
                "history",
                "incident",
                parsed,
                lessons,
            ),
        ]
        code = jobs[0].result()
        evidence_results = dict(job.result() for job in jobs[1:])

    findings = analyze(diff, lessons)
    verdict = (
        "BLOCK"
        if any(item["verdict"] == "BLOCK" for item in findings)
        else "UNKNOWN"
        if any(item["verdict"] == "UNKNOWN" for item in findings)
        else "PASS"
    )
    headlines = {
        "BLOCK": "Verified safeguard removed",
        "UNKNOWN": "Human review required",
        "PASS": "No verified safeguard conflict",
    }
    return {
        "scenario": "git-diff",
        "verdict": verdict,
        "headline": headlines[verdict],
        "agents": [
            {
                "agent_id": event["agent_id"],
                "provider": event["provider"],
                "branch": event["branch"],
                "status": "analyzed",
            }
            for event in events
        ],
        "scouts": {
            "code": code,
            "test": evidence_results["test"],
            "history": evidence_results["history"],
        },
        "findings": findings,
    }


def run_review(scenario: str, root: Path = Path(".")) -> dict:
    if scenario not in SCENARIO_DIFF:
        raise ValueError("scenario must be harmful, inferred, or safe")
    lessons = load_memory(root / "examples/project_memory.json")
    events = ingest(root / "examples/multi_agent_events.jsonl")
    selected_path = root / SCENARIO_DIFF[scenario]
    selected_diff = diff_text(selected_path)
    events[0] = {
        **events[0],
        "diff_path": SCENARIO_DIFF[scenario],
        "changed_paths": parse_diff(selected_diff)["paths"],
    }
    result = review_diff(selected_diff, events, lessons)
    result["scenario"] = scenario
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recallguard")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    source = check.add_mutually_exclusive_group(required=True)
    source.add_argument("--scenario", choices=tuple(SCENARIO_DIFF))
    source.add_argument("--diff")
    check.add_argument("--ai", action="store_true")
    args = parser.parse_args(argv)

    root = Path(__file__).parent
    if args.scenario:
        result = run_review(args.scenario, root)
    else:
        supplied_diff = sys.stdin.read() if args.diff == "-" else diff_text(
            Path(args.diff)
        )
        result = review_diff(
            supplied_diff,
            ingest(root / "examples/multi_agent_events.jsonl"),
            load_memory(root / "examples/project_memory.json"),
        )

    if args.ai:
        result["ai_status"] = "unavailable"
        if os.environ.get("OPENAI_API_KEY"):
            try:
                from agents_runtime import explain_scouts

                result["ai_explanations"] = explain_scouts(result["scouts"])
                result["ai_status"] = "complete"
            except Exception:
                pass

    print(json.dumps(result, indent=2))
    return {"PASS": 0, "UNKNOWN": 2, "BLOCK": 3}[result["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
