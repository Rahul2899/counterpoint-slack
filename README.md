# Counterpoint

Counterpoint is a Slack-native agent with one mandate: notice when a team is
closing a consequential decision without addressing a relevant concern, then
raise one evidence-backed objection.

It watches ordinary top-level messages in one public channel. Exa Agent decides
whether to abstain or object using the recent discussion and a small verified
team-memory fixture. Teammates keep control: a thumbs-down reaction dismisses
the objection, stops that decision from repeating, and updates a visible
decision-dissent ledger.

## What is working

- Ambient `message.channels` ingestion over Slack Socket Mode; no mention needed.
- One serialized worker with bounded deduplication, a 15-message window,
  cooldown, newest-window coalescing, and stale-result suppression.
- Exa Agent structured decisions with strict local schema, confidence, evidence,
  length, and unsafe-text validation.
- Native Slack objection card, `/dissent` manual evaluation, thumbs-down
  dismissal, and recoverable in-process ledger synchronization.
- Offline tests for Plan A, Plan B, and the combined contract.

## Requirements

- Python 3.12+
- A Slack workspace where you can install an app
- A funded Exa API key
- Outbound HTTPS and WebSocket access

## Install

```bash
git clone https://github.com/Rahul2899/counterpoint-slack.git
cd counterpoint-slack
python3 -m venv .venv
.venv/bin/python -m ensurepip --upgrade
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Keep `.env` local. Never paste credentials into issues, commits, screenshots, or
the demo video.

## Configure Slack

1. At <https://api.slack.com/apps>, create an app from
   `slack_app_manifest.yaml` and select the target workspace.
2. Under **Basic Information → App-Level Tokens**, generate an `xapp-` token
   with `connections:write`.
3. Install the app to the workspace and copy its `xoxb-` bot token.
4. Create or select one public demo channel and invite **Counterpoint Dev**.
5. Copy the channel ID from **Channel details → About**.
6. Confirm Socket Mode is enabled and the subscribed bot events are
   `message.channels` and `reaction_added`.

Fill `.env`:

```dotenv
SLACK_BOT_TOKEN=xoxb-replace-me
SLACK_APP_TOKEN=xapp-replace-me
SLACK_CHANNEL_ID=C0123456789
EXA_API_KEY=replace-me
COUNTERPOINT_COOLDOWN_SECONDS=90
```

The manifest grants only `channels:history`, `chat:write`, `reactions:read`, and
`commands`. Adding scopes later requires reinstalling the Slack app.

## Verify

Run the complete offline suite without using Slack or Exa:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Then verify the three synthetic scenarios against the real Exa Agent API:

```bash
.venv/bin/python counterpoint_agent.py premature_auth_consensus
.venv/bin/python counterpoint_agent.py healthy_auth_decision
.venv/bin/python counterpoint_agent.py irrelevant_memory
```

The first must object with the expected precedent. The other two must abstain.
The command exits nonzero on provider failure, suppressed output, wrong action,
or wrong evidence.

## Run

```bash
.venv/bin/python app.py
```

Expected startup message: `Bolt app is running!`

For a live rehearsal, have two humans send the `premature_auth_consensus`
messages from `demo_transcripts.json` as new top-level messages. Counterpoint
should post one objection. React with :thumbsdown: and verify the thread reply
and the single `Decision dissent ledger` message. Use `/dissent` only as a demo
fallback; it still cannot bypass agent abstention or evidence validation.

## Architecture

```text
Slack Socket Mode events
  -> channel, event-shape, and retry dedupe guards
  -> serialized 15-message runtime window
  -> Exa Agent run with complete synthetic team memory
  -> strict local decision/evidence validation
  -> stale-generation and cooldown gates
  -> Slack objection card
  -> thumbs-down dismissal and decision ledger
```

Semantic judgment remains agentic: Exa decides whether the group is closing a
decision prematurely, which memory is relevant, and what objection/question to
write. Deterministic code handles transport, provenance, safety, freshness,
rate control, and dismissal; it never invents an objection.

## Data and limitations

- `team_memory.json` and `demo_transcripts.json` are synthetic hackathon data.
- Slack text and the complete memory fixture are sent to Exa. Do not use private
  production conversations without appropriate workspace approval and data
  controls.
- Exa Agent is asynchronous and has no strict latency guarantee. Provider,
  timeout, malformed-output, or stale-window failures produce silence.
- Window, cooldown, pending objections, suppression, and ledger rows are held in
  memory and reset when the process restarts.
- A network timeout after Slack accepts a post can leave that post untracked.
  Durable exactly-once delivery is outside this prototype.
- The bot watches new events only; it does not backfill Slack history.

## Hackathon provenance

Counterpoint and its core functionality were built during the Agents,
Everywhere event. The project uses permitted building blocks: Python standard
library, Slack Bolt, python-dotenv, Exa Agent, reusable planning templates, and
synthetic fixtures. Earlier unrelated RecallGuard history supplied no
Counterpoint runtime or agent functionality.

`TEAM_SPLIT.md` records the completed two-person Plan A/Plan B development
split. Design and implementation decisions are under `docs/superpowers/`.
