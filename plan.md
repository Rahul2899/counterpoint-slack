# RecallGuard Four-Hour Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox ('- [ ]') syntax for tracking.

**Goal:** Build a working multi-agent merge guardian that catches a coding agent removing a verified safeguard, explains the evidence in an original mech-style browser interface, and leaves final authority with a human.

**Architecture:** A Python 3.12 core ingests vendor-neutral coding-agent events and unified diffs, runs Code, Test, and History scouts concurrently, then applies deterministic verdict rules. A standard-library HTTP server exposes the same result to a dependency-free HTML/CSS/JavaScript command center. OpenAI Agents SDK scouts may enrich summaries, but never control the verdict.

**Tech Stack:** Python 3.12, Python standard library, optional 'openai-agents', HTML, CSS, browser JavaScript, Git, unittest.

**Spec:** 'handoff.md'

## Global constraints

- Deadline: September 12, 2026 at 16:30 CEST. Reserve final 30 minutes for clean-clone verification, recording, and submission.
- Human remains highest authority. RecallGuard never edits source or merges automatically.
- Only an active, verified lesson with incident and test evidence can produce 'BLOCK'.
- Inferred or incomplete evidence produces 'UNKNOWN', never 'BLOCK'.
- OpenAI output may improve explanations; it cannot create evidence, confidence, or verdicts.
- Critical path runs without an API key.
- No exact Transformers characters, names, logos, voices, or Hasbro assets. Use an original mech visual language.
- No React, database, authentication, vector store, Docker, deployment, native vendor adapters, or 3D rendering.
- Freeze contracts before parallel work. Change them only with both builders present.

---

## Product and demo

**Problem:** Coding agents see what code is, but lose why safeguards exist. RecallGuard brings verified project experience into the merge boundary.

**60-second demo:**

1. Four events arrive from Claude Code, Codex, Gemini CLI, and Cursor.
2. Prime Coordinator dispatches Code, Test, and History scouts concurrently.
3. Code Scout finds removed webhook deduplication.
4. History Scout retrieves a verified duplicate-charge incident.
5. Test Scout links the regression test protecting the behavior.
6. Deterministic Guard Core returns 'BLOCK MERGE REVIEW'.
7. Browser gate closes and shows exact evidence.
8. Safe diff returns 'PASS' and opens the gate.

## Frontend specification

One screen: **RecallGuard Command Center**.

~~~text
repository + target branch
        ↓
four coding-agent branch cards
        ↓
Prime Coordinator
   ↙       ↓       ↘
Code     Test     History
Scout    Scout    Scout
        ↓
Guard Core
        ↓
PASS / UNKNOWN / BLOCK MERGE REVIEW
        ↓
human evidence controls
~~~

Required states:

- Idle: four agent cards visible; harmful run is primary.
- Scanning: three scout cards activate together.
- Block: red gate closes; removed line, lesson, incident, and test visible.
- Unknown: amber gate waits for human review.
- Pass: green gate opens.
- Failure: “Analysis unavailable — merge authority unchanged.”

Required controls: 'Run harmful merge', 'Run uncertain merge', 'Run safe merge', 'View evidence', 'Reset'.

Animation: CSS-only rail pulses, scout activation, and one gate transition. Respect 'prefers-reduced-motion'. No canvas, WebGL, video, or animation dependency.

## Deterministic backend contract

Verdict rules:

~~~text
active + verified + protected token removed + incident evidence + test evidence => BLOCK
active + inferred + relevant signal changed                               => UNKNOWN
relevant lesson missing required evidence                                 => UNKNOWN
superseded lesson                                                          => PASS
no relevant lesson                                                        => PASS
~~~

### Project memory

'examples/project_memory.json':

~~~json
{
  "version": 1,
  "lessons": [
    {
      "id": "PAY-001",
      "claim": "Webhook event IDs must be deduplicated before side effects.",
      "status": "active",
      "confidence": "verified",
      "paths": ["demo/src/webhook.py"],
      "block_if_removed": ["processed_event_ids", "event_id in processed_event_ids"],
      "warn_if_changed": [],
      "evidence": [
        {
          "kind": "incident",
          "ref": "INC-2026-04",
          "summary": "A retried webhook produced a duplicate charge."
        },
        {
          "kind": "test",
          "ref": "demo/tests/test_webhook.py::test_duplicate_event_is_ignored",
          "summary": "Regression test proves duplicate event IDs are ignored."
        }
      ]
    },
    {
      "id": "DOC-002",
      "claim": "External document processing may require redaction.",
      "status": "active",
      "confidence": "inferred",
      "paths": ["demo/src/parser.py"],
      "block_if_removed": [],
      "warn_if_changed": ["external_llm("],
      "evidence": []
    }
  ]
}
~~~

Reject the whole file with 'ValueError' when required keys are absent or enum values are invalid.

### Coding-agent event

Each line of 'examples/multi_agent_events.jsonl':

~~~json
{
  "agent_id": "claude-01",
  "provider": "claude-code",
  "task": "Simplify webhook processing",
  "branch": "agent/claude-webhook",
  "diff_path": "examples/remove_deduplication.diff",
  "changed_paths": ["demo/src/webhook.py"],
  "status": "ready"
}
~~~

Demo must contain exactly these providers:

~~~text
claude-code
codex
gemini-cli
cursor
~~~

'run_review' loads all four events, then sets the first event's diff from this fixed map:

~~~python
SCENARIO_DIFF = {
    "harmful": "examples/remove_deduplication.diff",
    "inferred": "examples/inferred_external_llm.diff",
    "safe": "examples/safe_change.diff",
}
~~~

The remaining three events use the safe fixture. This keeps four incoming coding agents visible while changing only the proposal under review.

### Review response

CLI and HTTP use this exact shape:

~~~json
{
  "scenario": "harmful",
  "verdict": "BLOCK",
  "headline": "Verified safeguard removed",
  "agents": [
    {
      "agent_id": "claude-01",
      "provider": "claude-code",
      "branch": "agent/claude-webhook",
      "status": "analyzed"
    }
  ],
  "scouts": {
    "code": {
      "status": "complete",
      "summary": "Protected webhook deduplication tokens were removed.",
      "evidence": ["-if event_id in processed_event_ids:"]
    },
    "test": {
      "status": "complete",
      "summary": "A linked regression test protects this behavior.",
      "evidence": ["demo/tests/test_webhook.py::test_duplicate_event_is_ignored"]
    },
    "history": {
      "status": "complete",
      "summary": "Active verified lesson PAY-001 matches this path.",
      "evidence": ["INC-2026-04"]
    }
  },
  "findings": [
    {
      "lesson_id": "PAY-001",
      "confidence": "verified",
      "status": "active",
      "verdict": "BLOCK",
      "reason": "Webhook deduplication protected by incident and test evidence was removed.",
      "removed_lines": ["-if event_id in processed_event_ids:"],
      "evidence_refs": [
        "INC-2026-04",
        "demo/tests/test_webhook.py::test_duplicate_event_is_ignored"
      ]
    }
  ]
}
~~~

Safe response keeps every top-level key, uses 'verdict: "PASS"', and has an empty 'findings' array.

### HTTP API

~~~text
GET  /api/health
200  {"status":"ok"}

POST /api/review
body {"scenario":"harmful"}, {"scenario":"inferred"}, or {"scenario":"safe"}
200  review response

unknown scenario
400  {"error":"scenario must be harmful, inferred, or safe"}
~~~

## File ownership

Person A — core and agents:

- 'requirements.txt'
- 'recallguard.py'
- 'agents_runtime.py'
- 'examples/*'
- 'tests/test_recallguard.py'
- main-branch integration

Person B — server, frontend, and demo:

- 'server.py'
- 'web/index.html'
- 'web/styles.css'
- 'web/app.js'
- 'tests/test_server.py'
- 'README.md'

Frozen after work begins: 'plan.md', 'handoff.md'. No same-file concurrent edits.

---

## Task 1: Person A locks fixtures and validation

**Files:**
- Create: 'recallguard.py'
- Create: 'examples/project_memory.json'
- Create: 'examples/multi_agent_events.jsonl'
- Create: 'examples/remove_deduplication.diff'
- Create: 'examples/inferred_external_llm.diff'
- Create: 'examples/safe_change.diff'
- Create: 'tests/test_recallguard.py'

**Produces:**
- 'load_memory(path: Path) -> list[dict]'
- 'ingest(path: Path) -> list[dict]'
- 'diff_text(path: Path) -> str'

- [ ] Write failing validation and four-provider tests:

~~~python
def test_memory_requires_complete_lessons(self):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "bad.json"
        path.write_text('{"version":1,"lessons":[{"id":"bad"}]}')
        with self.assertRaises(ValueError):
            recallguard.load_memory(path)

def test_demo_ingests_four_providers(self):
    events = recallguard.ingest(Path("examples/multi_agent_events.jsonl"))
    self.assertEqual({event["provider"] for event in events}, {
        "claude-code", "codex", "gemini-cli", "cursor"
    })
~~~

- [ ] Run 'python3.12 -m unittest tests.test_recallguard -v'. Expected: import failure.
- [ ] Implement JSON and JSONL loading with these exact constants:

~~~python
REQUIRED_LESSON_KEYS = {
    "id", "claim", "status", "confidence", "paths",
    "block_if_removed", "warn_if_changed", "evidence"
}
VALID_STATUS = {"active", "superseded"}
VALID_CONFIDENCE = {"verified", "inferred", "unknown"}
~~~

- [ ] Raise 'ValueError' on malformed JSON, missing keys, invalid enums, or non-list 'lessons'.
- [ ] Rerun tests. Expected: pass.
- [ ] Commit:

~~~bash
git add recallguard.py examples tests/test_recallguard.py
git commit -m "feat: lock RecallGuard evidence contracts"
git push -u origin person-a/core
~~~

## Task 2: Person A builds scouts and gate

**Files:**
- Modify: 'recallguard.py'
- Modify: 'tests/test_recallguard.py'

**Produces:**
- 'parse_diff(text: str) -> dict[str, list[str]]'
- 'analyze(diff: str, lessons: list[dict]) -> list[dict]'
- 'review_diff(diff: str, events: list[dict], lessons: list[dict]) -> dict'
- 'run_review(scenario: str, root: Path = Path(".")) -> dict'

- [ ] Add failing harmful, safe, and inferred tests:

~~~python
def test_verified_conflict_blocks(self):
    result = recallguard.run_review("harmful")
    self.assertEqual(result["verdict"], "BLOCK")
    self.assertEqual(result["findings"][0]["lesson_id"], "PAY-001")

def test_safe_change_passes(self):
    result = recallguard.run_review("safe")
    self.assertEqual(result["verdict"], "PASS")
    self.assertEqual(result["findings"], [])

def test_inferred_lesson_never_blocks(self):
    diff = (
        "diff --git a/demo/src/parser.py b/demo/src/parser.py\n"
        "+++ b/demo/src/parser.py\n"
        "+external_llm(document_text)\n"
    )
    findings = recallguard.analyze(diff, self.lessons)
    self.assertEqual(findings[0]["verdict"], "UNKNOWN")
~~~

- [ ] Parse paths only from '+++ b/', removals from '-' excluding '---', additions from '+' excluding '+++'.
- [ ] Run the three deterministic scouts concurrently:

~~~python
with ThreadPoolExecutor(max_workers=3) as pool:
    futures = {
        "code": pool.submit(code_scout, events),
        "test": pool.submit(test_scout, lessons),
        "history": pool.submit(history_scout, events, lessons),
    }
    scouts = {name: future.result() for name, future in futures.items()}
~~~

- [ ] Apply verdict precedence:

~~~python
verdict = (
    "BLOCK" if any(item["verdict"] == "BLOCK" for item in findings)
    else "UNKNOWN" if any(item["verdict"] == "UNKNOWN" for item in findings)
    else "PASS"
)
~~~

- [ ] Run all core tests. Expected: five tests pass.
- [ ] Commit and push:

~~~bash
git add recallguard.py tests/test_recallguard.py
git commit -m "feat: add parallel scouts and deterministic gate"
git push
~~~

## Task 3: Person A adds Git CLI and optional OpenAI scouts

**Files:**
- Create: 'requirements.txt'
- Create: 'agents_runtime.py'
- Modify: 'recallguard.py'
- Modify: 'tests/test_recallguard.py'

**Produces:**
- CLI demo: 'python recallguard.py check --scenario harmful [--ai]'
- CLI Git path: 'git diff --cached | python recallguard.py check --diff - [--ai]'
- 'explain_scouts(scouts: dict) -> dict[str, str]'

- [ ] Add a safe CLI smoke test using 'subprocess.run' and JSON parsing.
- [ ] Add a stdin test that passes the harmful fixture to 'check --diff -' and expects exit 3 plus a BLOCK JSON body.
- [ ] Implement mutually exclusive '--scenario harmful|inferred|safe' and '--diff PATH'. A diff path of '-' reads stdin.
- [ ] Exit 0 PASS, 2 UNKNOWN, 3 BLOCK, 1 runtime/input failure.
- [ ] Add 'openai-agents' as the only line in 'requirements.txt'.
- [ ] Implement parallel OpenAI summaries:

~~~python
async def explain_scouts(scouts):
    names = ("code", "test", "history")
    jobs = []
    for name in names:
        agent = Agent(
            name=f"{name.title()} Scout",
            instructions=(
                "Explain supplied deterministic evidence in one sentence. "
                "Do not add facts, evidence, confidence, or a verdict."
            ),
        )
        jobs.append(Runner.run(agent, json.dumps(scouts[name])))
    results = await asyncio.gather(*jobs)
    return {
        name: result.final_output
        for name, result in zip(names, results, strict=True)
    }
~~~

- [ ] Import 'agents_runtime' only for '--ai'. Missing package/key, timeout, or model failure retains deterministic text and adds '{"ai_status":"unavailable"}'.
- [ ] Verify no-key path:

~~~bash
env -u OPENAI_API_KEY python3.12 -m unittest tests.test_recallguard -v
python3.12 recallguard.py check --scenario harmful
~~~

Expected: tests pass; harmful command exits 3 and prints BLOCK.

- [ ] If a key is available, verify AI path in a Python 3.12 virtual environment:

~~~bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python recallguard.py check --scenario safe --ai
~~~

Expected: PASS remains PASS and 'ai_status' is 'complete'.

- [ ] Commit, push, and open the core pull request.

## Task 4: Person B builds HTTP adapter

**Files:**
- Create: 'server.py'
- Create: 'tests/test_server.py'

**Consumes:** 'recallguard.run_review(scenario: str) -> dict'

**Produces:** 'review_response(scenario: str) -> tuple[int, dict]'

- [ ] Write helper tests without opening a port:

~~~python
def test_harmful_review_contract(self):
    status, body = server.review_response("harmful")
    self.assertEqual(status, 200)
    self.assertEqual(body["verdict"], "BLOCK")

def test_unknown_scenario_rejected(self):
    status, body = server.review_response("other")
    self.assertEqual(status, 400)
    self.assertEqual(body, {
        "error": "scenario must be harmful, inferred, or safe"
    })
~~~

- [ ] Until core merges, keep the frozen sample response in one 'SAMPLE_RESULTS' dictionary inside 'server.py'. Delete it during integration.
- [ ] Implement 'ThreadingHTTPServer' and 'BaseHTTPRequestHandler':
  - bind '127.0.0.1:8000';
  - accept at most 1024 request bytes;
  - require JSON for POST;
  - set 'Content-Length';
  - serve only 'index.html', 'styles.css', and 'app.js';
  - reject path traversal and unknown routes.
- [ ] Run 'python3.12 -m unittest tests.test_server -v'. Expected: pass.
- [ ] Commit and push to 'person-b/frontend'.

## Task 5: Person B builds Command Center

**Files:**
- Create: 'web/index.html'
- Create: 'web/styles.css'
- Create: 'web/app.js'

- [ ] Build semantic HTML:

~~~html
<main>
  <header><!-- product, repository, target branch --></header>
  <section id="agents" aria-label="Coding agent branches"></section>
  <section id="coordinator" aria-live="polite"></section>
  <section id="scouts" aria-label="Evidence scouts"></section>
  <section id="gate" aria-live="assertive"></section>
  <section id="evidence" hidden></section>
  <nav><!-- harmful, inferred, safe, evidence, reset --></nav>
</main>
~~~

- [ ] Use this CSS palette:

~~~css
:root {
  --ink: #0b1220;
  --panel: #f4f1e8;
  --steel: #c8d0d8;
  --signal: #ffb000;
  --pass: #147d64;
  --unknown: #a96800;
  --block: #b42318;
}
~~~

- [ ] Implement one browser state machine:

~~~javascript
async function runScenario(scenario) {
  render({ phase: "scanning", result: null, selectedScenario: scenario });
  const response = await fetch("/api/review", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scenario }),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Review failed");
  render({
    phase: body.verdict.toLowerCase(),
    result: body,
    selectedScenario: scenario,
  });
}
~~~

- [ ] Render API values with 'textContent', never 'innerHTML'.
- [ ] Add visible keyboard focus, non-color status text, reduced-motion CSS, 390px layout, and failed-fetch state.
- [ ] Commit and push.

## Task 6: Integrate branches

**Owner:** Person A drives. Person B fixes frontend-owned files only.

- [ ] Merge the core pull request to 'main'.
- [ ] Person B rebases 'person-b/frontend' onto 'origin/main'.
- [ ] Delete 'SAMPLE_RESULTS' and connect:

~~~python
from recallguard import run_review

def review_response(scenario):
    if scenario not in {"harmful", "inferred", "safe"}:
        return 400, {
            "error": "scenario must be harmful, inferred, or safe"
        }
    return 200, run_review(scenario)
~~~

- [ ] Run:

~~~bash
python3.12 -m unittest discover -s tests -v
python3.12 server.py
~~~

- [ ] Browser-check harmful, inferred, safe, evidence, reset, failure, keyboard, reduced motion, and 390px width.
- [ ] Person B opens frontend pull request; Person A reviews and merges.

## Task 7: README, clean clone, and submission

**Owner:** Person B writes. Person A verifies.

**Files:**
- Create: 'README.md'

- [ ] README sections: problem, 60-second demo, architecture, trust rules, local setup, deterministic fallback, OpenAI enhancement, known limitations.
- [ ] State these limitations exactly:
  - vendor-neutral fixtures, not four native adapters;
  - repository-authored path/token guards, not general semantic understanding;
  - browser is visualization/control; diff inspection is the work environment;
  - no autonomous edits, merges, or lesson mutation.
- [ ] Clean-clone verification:

~~~bash
cd /tmp
git clone https://github.com/Rahul2899/recallguard.git recallguard-clean
cd recallguard-clean
/opt/anaconda3/bin/python3.12 -m unittest discover -s tests -v
~~~

- [ ] Record: four agents → harmful run → three scouts → exact evidence → deterministic boundary → safe run.
- [ ] Submit repository URL, video, team members, OpenAI Agents SDK use, and honest limitations.

---

## Two-person clock

| Elapsed | Person A — core | Person B — interface |
|---:|---|---|
| 0:00–0:15 | Fixtures, schemas, failing tests | HTML shell, frozen response |
| 0:15–1:00 | Diff parser, verdict rules | CSS command center |
| 1:00–1:30 | Parallel scouts, CLI | JavaScript states |
| 1:30–2:00 | Optional OpenAI agents | HTTP server and tests |
| 2:00–2:30 | Merge core, integration support | Rebase, connect core |
| 2:30–3:00 | Full tests, failure checks | Browser/accessibility smoke |
| 3:00–3:30 | Clean-clone verification | README, rehearsal |
| 3:30–4:00 | Record and submit | Record and submit |

If less than four hours remain, preserve the final 30 minutes and cut in order:

1. OpenAI-generated wording; keep deterministic parallel scouts.
2. Nonessential animation.
3. Pre-push hook.
4. Safe-run animation polish.

Never cut harmful BLOCK, safe PASS, UNKNOWN, evidence provenance, tests, or clean-clone verification.

## Definition of done

- Private GitHub repository cloneable by both builders.
- Four provider records ingested and visible.
- Three scouts execute concurrently.
- Harmful diff returns BLOCK with removed line, incident, and test evidence.
- Safe diff returns PASS.
- Inferred lesson returns UNKNOWN and never blocks.
- Browser renders idle, scanning, block, pass, unknown, and failure through runnable scenarios.
- Deterministic tests pass without API key.
- OpenAI failure cannot alter verdict.
- README separates fixtures from implemented integration.
- Clean clone works; demo completes within 75 seconds.

## Explicitly skipped

- Literal Optimus Prime or Transformers IP.
- Generic chat UI or dashboard navigation.
- Native adapters for four coding tools.
- Automatic lesson creation.
- Arbitrary commands from the ledger.
- Autonomous merge, source edit, or override.
- Hosted deployment.

Add skipped items only after submission and only when a real user need proves them.
