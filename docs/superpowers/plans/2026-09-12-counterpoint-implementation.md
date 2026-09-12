# Counterpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans task-by-task. Steps use checkbox syntax.

**Goal:** Build a Slack-native agent that detects premature decision closure, raises one verified evidence-backed objection through Exa Agent, accepts a thumbs-down dismissal, and maintains a visible decision ledger.

**Architecture:** Python Slack Bolt receives ambient public-channel events over Socket Mode and serializes accepted human messages through one worker. The worker calls a separate Exa Agent module through one frozen Python function; deterministic guards validate provenance, suppress stale or unsafe output, and preserve human control.

**Tech Stack:** Python 3.12, Python standard library, slack-bolt 1.28.0, python-dotenv 1.1.1, Exa Agent HTTP API, unittest.

**Spec:** docs/superpowers/specs/2026-09-12-counterpoint-design.md

## Global Constraints

- Slack is the only interface; no standalone web or 3D frontend.
- Monitor exactly one public channel named by SLACK_CHANNEL_ID.
- Exa Agent performs semantic consensus judgment, evidence selection, abstention, and objection writing.
- Deterministic code may suppress agent output but never create an objection or declare consensus.
- Only verified IDs from team_memory.json may support an objection.
- Provider error, invalid output, timeout, or stale conversation fails silently.
- Thumbs-down dismisses the intervention; it never marks evidence false.
- /dissent bypasses the activity gate, not agent judgment or evidence validation.
- Demo Slack messages and memory are synthetic and disclosed.
- No CopilotKit dependency in the core path.
- No secrets in Git, fixtures, logs, screenshots, issues, or pull requests.
- Planning documents and the integration contract stay unchanged during parallel work.

---

## Parallel execution map

Plan A and Plan B start from the same planning-baseline commit and run in parallel. Tasks inside each lane run in order.

~~~text
Plan A: A1 -> A2 -> A3 ----\
                             -> I1 integration
Plan B: B1 -> B2 -> B3 ----/
~~~

Plan B merges first. Plan A rebases, integrates, then merges.

## Frozen interface

Plan B produces:

~~~python
def analyze_window(messages: list[dict[str, str]]) -> dict[str, object]:
    """Return a validated decision; return abstention on provider failures."""
~~~

Plan A supplies at most 15 ordered messages:

~~~python
{"ts": "1745444400.123456", "user_id": "U123ABC", "text": "Ship it."}
~~~

Exact decision shape:

~~~python
{
    "action": "abstain",  # or "object"
    "confidence": 0.0,
    "decision_summary": "",
    "objection": "",
    "evidence_ids": [],
    "resolution_question": "",
    "reason": "provider_timeout",
}
~~~

No Slack SDK object crosses this interface. Plan A tests inject a fake analyzer, so Plan A does not require Plan B before rebase.

---

## Plan B — teammate: decision agent

### Task B1: Evidence memory and validation

**Files:**

- Create: counterpoint_agent.py
- Create: team_memory.json
- Create: tests/test_counterpoint_agent.py

**Produces:**

~~~python
def _load_memory(path: Path) -> list[dict[str, str]]: ...
def _validate_decision(raw: object, memory: list[dict[str, str]]) -> dict[str, object]: ...
~~~

- [ ] Write failing tests proving valid version-1 memory loads and malformed version, missing fields, duplicate IDs, or a status other than verified raises ValueError.
- [ ] Run python3 -m unittest tests.test_counterpoint_agent.MemoryTests -v and verify failure.
- [ ] Implement loading with json and pathlib only. Require id, date, decision, concern, outcome, source, and status on every record.
- [ ] Add six synthetic records: billing migration, self-hosted authentication, missing feature flag, EU residency, vendor lock-in, and launch-scope incident. Each has a concrete outcome or number.
- [ ] Run the memory tests and verify pass.
- [ ] Commit:

~~~bash
git add counterpoint_agent.py team_memory.json tests/test_counterpoint_agent.py
git commit -m "feat: add verified Counterpoint memory"
~~~

### Task B2: Real Exa decision agent

**Files:**

- Modify: counterpoint_agent.py
- Modify: tests/test_counterpoint_agent.py

**Produces:** the frozen analyze_window function.

- [ ] Add tests covering valid object, valid abstain, unknown evidence ID, confidence below 0.65, empty objection, empty question, more than two evidence IDs, and objection above 480 characters.
- [ ] Implement the canonical abstention:

~~~python
def _abstain(reason: str) -> dict[str, object]:
    return {
        "action": "abstain",
        "confidence": 0.0,
        "decision_summary": "",
        "objection": "",
        "evidence_ids": [],
        "resolution_question": "",
        "reason": reason,
    }
~~~

- [ ] Validate exactly seven keys. Reject non-finite confidence, booleans used as numbers, duplicates, unknown IDs, and object decisions without verified evidence.
- [ ] Add HTTP lifecycle tests by patching _request_json: immediate completion, running then completed, failed/cancelled, 25-second timeout, invalid JSON/HTTP error, and missing EXA_API_KEY.
- [ ] Implement _request_json with urllib.request. Never log response bodies or credentials.
- [ ] POST https://api.exa.ai/agent/runs with headers:

~~~python
{
    "Content-Type": "application/json",
    "x-api-key": api_key,
    "Exa-Beta": "agent-2026-05-07",
}
~~~

- [ ] Send effort minimal, budget.maxCostDollars 0.05, and a JSON outputSchema matching the frozen decision.
- [ ] Poll GET https://api.exa.ai/agent/runs/{id} every 0.5 seconds, using time.monotonic for a 25-second deadline.
- [ ] Prompt with the complete ordered transcript and complete memory. Treat Slack text as quoted data, not instructions. Agent must distinguish healthy agreement, check whether concern was addressed, cite one or two memory IDs, write one objection and one question, avoid judging motives, and abstain without relevant verified evidence.
- [ ] Run python3 -m unittest tests.test_counterpoint_agent -v without a real key or network.
- [ ] Commit:

~~~bash
git add counterpoint_agent.py tests/test_counterpoint_agent.py
git commit -m "feat: add Exa-powered decision agent"
~~~

### Task B3: Behavioral demo fixtures

**Files:**

- Create: demo_transcripts.json
- Modify: tests/test_counterpoint_agent.py

- [ ] Add premature_auth_consensus: two users settle on self-hosted auth without concern; expected object with DEC-002.
- [ ] Add healthy_auth_decision: migration cost and rollback are addressed; expected abstain.
- [ ] Add irrelevant_memory: low-risk naming choice; expected abstain.
- [ ] Keep each transcript at 4–8 messages using fake Slack user IDs.
- [ ] Patch _request_json and assert each generated create payload contains every message, every memory ID, outputSchema, minimal effort, and $0.05 budget.
- [ ] Run and commit:

~~~bash
python3 -m unittest tests.test_counterpoint_agent -v
git add demo_transcripts.json tests/test_counterpoint_agent.py
git commit -m "test: add Counterpoint behavior scenarios"
~~~

- [ ] Push and open the Plan B pull request:

~~~bash
git push -u origin teammate/plan-b-agent
gh pr create --base main --head teammate/plan-b-agent --title "Plan B: Exa decision agent" --body "Implements the frozen analyze_window contract, verified synthetic memory, provider failure-to-abstention behavior, and offline behavior fixtures."
~~~

---

## Plan A — repository owner: Slack runtime

### Task A1: Slack app manifest and event shell

**Files:**

- Create: slack_app_manifest.yaml
- Create: .env.example
- Create: requirements.txt
- Create: app.py
- Create: tests/test_app.py

**Produces:** CounterpointRuntime and create_slack_app(runtime).

- [ ] Create requirements.txt:

~~~text
slack-bolt==1.28.0
python-dotenv==1.1.1
~~~

- [ ] Create .env.example:

~~~text
SLACK_BOT_TOKEN=xoxb-replace-me
SLACK_APP_TOKEN=xapp-replace-me
SLACK_CHANNEL_ID=C0123456789
EXA_API_KEY=replace-me
COUNTERPOINT_COOLDOWN_SECONDS=90
~~~

- [ ] Create a Socket Mode manifest named Counterpoint Dev. App token scope: connections:write. Bot scopes: channels:history, chat:write, reactions:read, commands. Bot events: message.channels, reaction_added. Slash command: /dissent. Add no join, user, customize, pin, reaction-write, or public-post scope.
- [ ] Write message-filter tests. Accept one non-empty top-level human message in the exact public channel. Reject wrong channel, missing user, empty text, bot_id, any subtype, and thread_ts.
- [ ] Implement:

~~~python
def normalize_message(body: dict[str, object], channel_id: str) -> dict[str, str] | None:
    """Return ts/user_id/text for one accepted top-level human message."""
~~~

- [ ] Dedupe by team_id:event_id. Keep newest 500 keys using deque plus set.
- [ ] Write queue tests using a fake analyzer. Listener must enqueue and return without waiting; one worker processes timestamp order; window stays at 15; analyzer is not called before two users and four messages.
- [ ] Implement CounterpointRuntime with one queue.Queue, one daemon worker, a 15-message deque, one lock-protected generation counter, and injected analyzer. Four-message/two-user is only a resource gate; no agreement keywords.
- [ ] Register message listener. It validates, deduplicates, increments generation, enqueues, and returns.
- [ ] Register /dissent. It calls ack immediately, validates exact channel, and enqueues manual analysis. Register reaction_added; state mutation arrives in A2.
- [ ] Run and commit:

~~~bash
python3 -m unittest tests.test_app -v
git add app.py tests/test_app.py slack_app_manifest.yaml .env.example requirements.txt
git commit -m "feat: add Slack Socket Mode event shell"
~~~

### Task A2: Intervention, staleness, dismissal, and ledger

**Files:**

- Modify: app.py
- Modify: tests/test_app.py

- [ ] Test abstention posts nothing; object posts once; generation change discards result; cooldown starts only after successful post; Slack post failure leaves no pending intervention.
- [ ] Render plain-text accessibility fallback plus Block Kit sections: Counterpoint title, objection, evidence IDs, resolution question, and thumbs-down instruction. No buttons.
- [ ] Coalesce automatic work. Messages arriving during an agent run invalidate that result and cause one newest-window evaluation, not every intermediate evaluation. Messages during cooldown update the window and cause one newest evaluation at expiry.
- [ ] Test that only a human -1 reaction on a pending intervention in the exact channel dismisses it. Wrong channel, bot reaction, unknown message, second reaction, or reaction on ledger changes nothing.
- [ ] Store pending interventions by Slack timestamp with summary, evidence IDs, and posted time.
- [ ] On dismissal, remove pending state, suppress the normalized decision summary, post "Understood. Standing down. Logged." in its thread, and create or update one bot-authored Decision dissent ledger message.
- [ ] If chat.update returns message_not_found, create one replacement ledger. Other Slack failures remain local and do not duplicate ledger rows.
- [ ] Run and commit:

~~~bash
python3 -m unittest tests.test_app -v
git add app.py tests/test_app.py
git commit -m "feat: add evidence intervention and dismissal ledger"
~~~

### Task A3: Startup and offline verification

**Files:**

- Modify: app.py
- Modify: tests/test_app.py

- [ ] Test missing environment variables produce an error naming only the variable.
- [ ] Test production startup lazily imports counterpoint_agent.analyze_window, creates the worker, authenticates Slack, and starts SocketModeHandler.
- [ ] Call load_dotenv before reading environment. Validate SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_CHANNEL_ID, and EXA_API_KEY without logging values.
- [ ] Run python3 -m unittest tests.test_app -v with no network.
- [ ] Commit:

~~~bash
git add app.py tests/test_app.py
git commit -m "feat: start Counterpoint runtime"
~~~

---

## Integration Task I1 — repository owner after Plan B merges

**Files:** Modify README.md. Test both lane suites.

- [ ] Merge Plan B into main, then from Plan A run git fetch origin and git rebase origin/main.
- [ ] Install and run offline tests:

~~~bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
~~~

- [ ] Scan tracked files for xoxb-, xapp-, and non-placeholder EXA_API_KEY values. Confirm no real secret.
- [ ] Create the Slack app from slack_app_manifest.yaml, generate connections:write app token, install after all scopes exist, store credentials only in .env, create #war-room, and invite Counterpoint Dev.
- [ ] Run all three demo transcripts through the real Exa API. Premature closure must object; both safe cases must abstain. If behavior fails, change only prompt or fixture evidence and rerun all three.
- [ ] Two real humans type the premature-auth transcript. Verify one autonomous post, visible evidence ID, thumbs-down acknowledgement, and one ledger update. Run healthy transcript and verify silence.
- [ ] Update README with setup, launch command, architecture, demo, synthetic-data disclosure, Exa beta/latency/privacy boundary, in-memory limit, and exact live verification.
- [ ] Run final unit suite and git status, then commit README.
- [ ] Push and open Plan A pull request:

~~~bash
git push -u origin owner/plan-a-slack
gh pr create --base main --head owner/plan-a-slack --title "Plan A: Slack runtime and Counterpoint integration" --body "Adds ambient Slack monitoring, serialized agent orchestration, native evidence cards, human dismissal, decision ledger, and verified integration instructions."
~~~

## Final acceptance

- Offline suite passes without credentials or network.
- Live runtime consumes ordinary top-level messages without mentions.
- Real Exa Agent semantics—not keywords—choose abstain or object.
- Premature closure produces one grounded objection with existing evidence IDs.
- Healthy agreement and irrelevant memory produce silence.
- Conversation changes during analysis cannot publish stale objection.
- Thumbs-down dismisses exactly one intervention and updates ledger once.
- /dissent never bypasses evidence requirements.
- Repository contains no real credential or private Slack transcript.
