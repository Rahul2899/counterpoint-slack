# Counterpoint Design

**Date:** 2026-09-12

## Product decision

Build Counterpoint as a Slack-native agent. It observes ordinary conversation
in one allowlisted public channel and acts only when an actual multi-person
interaction creates the signal: a consequential decision appears to be closing
without a relevant concern being resolved.

The central value cannot be reproduced in a private chatbot. Counterpoint acts
on group behavior and on what the group did not say.

## User experience

1. Two or more teammates discuss a concrete choice in Slack.
2. Counterpoint silently accumulates the last 15 human messages.
3. A cheap activity gate decides whether enough new multi-person context exists
   to justify an agent run. This gate does not decide that consensus exists.
4. Exa Agent receives the transcript and the complete synthetic team-memory
   fixture. It chooses `abstain` or `object`, selects evidence, and writes one
   concise objection plus one resolution question.
5. Valid output appears as a native Slack message with evidence IDs and the
   footer `React :thumbsdown: to dismiss and record.`
6. A thumbs-down reaction makes the agent acknowledge the dismissal, prevents
   repetition of that objection, and updates one bot-authored decision ledger.

Counterpoint never answers questions, gives general advice, diagnoses people,
or keeps arguing after dismissal.

## Architecture

~~~text
Slack Socket Mode
    -> exact-channel and human-message filter
    -> bounded event deduplication
    -> single serialized worker queue
    -> rolling 15-message window and activity gate
    -> Exa Agent run with structured output
    -> schema, evidence-ID, confidence, and staleness validation
    -> Slack evidence card
    -> thumbs-down dismissal
    -> bot-authored decision ledger
~~~

One long-running Python process owns the runtime. State is deliberately
in-memory for the hackathon. Slack remains the visible record after an
intervention is posted.

## Agentic boundary

Counterpoint is not a deterministic keyword bot.

Exa Agent decides:

- whether the discussion contains a concrete consequential decision;
- whether agreement is premature or a concern was already addressed;
- which prior memory records are relevant;
- whether evidence is strong enough to justify interruption;
- how to express one objection and one resolution question;
- when to abstain.

Application code decides only:

- which channel and event types are allowed;
- whether enough human activity exists to spend an agent call;
- cooldown, deduplication, timeout, and cost limits;
- whether returned JSON satisfies the frozen contract;
- whether every cited evidence ID exists in the supplied memory;
- whether the conversation changed while the asynchronous run was executing;
- how user dismissal is recorded.

A deterministic layer may suppress unsafe output. It may never turn an agent
`abstain` result into an objection or declare consensus itself.

## Reasoning provider

The first implementation uses Exa Agent API:

- `POST https://api.exa.ai/agent/runs`
- `GET https://api.exa.ai/agent/runs/{id}`
- header `x-api-key: $EXA_API_KEY`
- header `Exa-Beta: agent-2026-05-07`
- `effort: minimal`
- `budget.maxCostDollars: 0.05`
- a strict `outputSchema`
- 25-second application timeout

Exa Agent is asynchronous and beta. Timeout, invalid output, provider failure,
or stale conversation produces silence. The demo channel contains synthetic,
non-sensitive information because supplied Slack text leaves the workspace and
the API does not provide a documented hard switch disabling all web research.

CopilotKit Intelligence is excluded from the core path. Its project key adds
persistence and operational features but does not supply model inference.

## Frozen integration contract

Plan B owns this function:

~~~python
def analyze_window(messages: list[dict[str, str]]) -> dict[str, object]:
    """Return a validated agent decision; never raise for provider failures."""
~~~

Each message has exactly:

~~~json
{"ts": "1745444400.123456", "user_id": "U123ABC", "text": "Ship it."}
~~~

The function returns exactly these keys:

~~~json
{
  "action": "abstain",
  "confidence": 0.0,
  "decision_summary": "",
  "objection": "",
  "evidence_ids": [],
  "resolution_question": "",
  "reason": "provider_timeout"
}
~~~

`action` is `abstain` or `object`. An `object` decision is valid only when:

- confidence is at least `0.65`;
- `decision_summary`, `objection`, and `resolution_question` are non-empty;
- `objection` is at most 480 characters;
- `evidence_ids` contains one or two IDs present in `team_memory.json`;
- no cited record has `status` other than `verified`.

Any violation becomes the canonical abstention response. Plan A may depend only
on this function and return shape.

## Team-memory contract

`team_memory.json` contains six to ten synthetic records:

~~~json
{
  "version": 1,
  "records": [
    {
      "id": "DEC-001",
      "date": "2026-04-19",
      "decision": "Move billing storage from Postgres to DynamoDB",
      "concern": "Kai reported that the backfill and rollback were not costed",
      "outcome": "A four-day estimate became 21 days and the change was rolled back",
      "source": "Synthetic demo decision record",
      "status": "verified"
    }
  ]
}
~~~

The complete file is sent to the agent because it is smaller than the context
window. No embeddings, vector database, search index, or Slack history backfill.

## Slack contract

App display name: `Counterpoint Dev`.

Socket Mode app token scope:

- `connections:write`

Bot scopes:

- `channels:history`
- `chat:write`
- `reactions:read`
- `commands`

Bot events:

- `message.channels`
- `reaction_added`

Slash command: `/dissent`. It bypasses the activity gate, not evidence
validation. If the agent abstains, the caller receives an ephemeral notice.

The bot must be invited to the allowlisted channel. It ignores messages with a
subtype, a `bot_id`, a missing user, or a channel other than
`SLACK_CHANNEL_ID`. The first build intentionally ignores thread replies; the
demo conversation uses top-level channel messages.

## Concurrency and stale-result policy

Slack listeners enqueue work and return immediately. One worker consumes events
serially. The listener increments a protected generation counter for every
accepted human message. Each agent run captures its starting generation. If the
generation changed before the result is ready, the worker discards the result
and evaluates the newer window on the next queued event.

This prevents an asynchronous agent from objecting to a conversation that has
already moved on or addressed the concern.

## Human control and ledger

Only thumbs-down on a pending Counterpoint message dismisses it. The ledger
records:

- intervention timestamp;
- decision summary;
- evidence IDs;
- dismissing Slack user;
- status `dismissed`.

Dismissal does not mark the objection false and does not delete evidence. The
bot replies in the intervention thread: `Understood. Standing down. Logged.`

## Failure behavior

- Missing environment variable: fail startup with the variable name, never its
  value.
- Exa timeout, HTTP failure, invalid JSON, or failed run: abstain and log locally.
- Invalid or unknown evidence ID: abstain.
- Stale result: discard without posting.
- Slack posting failure: retain no pending intervention.
- Duplicate Slack event or duplicate thumbs-down: no duplicate post or ledger row.
- Agent already on cooldown: keep collecting messages and remain silent.

Secrets never enter Git, test fixtures, screenshots, issue text, or pull-request
descriptions.

## Frontend

Slack is the only frontend. Counterpoint posts a compact native Block Kit card
with a plain-text fallback for accessibility:

- title: `Counterpoint`;
- objection;
- evidence IDs and dates;
- resolution question;
- dismissal instruction.

Original mech language may appear in the icon and copy, but no Transformers,
Optimus Prime, Hasbro names, assets, or imitations.

## Acceptance scenarios

1. Premature closure: agent independently returns `object`, cites a verified
   memory ID, and posts once.
2. Healthy agreement: agent returns `abstain`; Slack stays silent.
3. Concern already raised: agent returns `abstain`.
4. Irrelevant memory: agent returns `abstain` rather than inventing precedent.
5. Provider error: Slack stays silent and process remains alive.
6. Conversation changes during analysis: stale result is discarded.
7. Thumbs-down: agent stands down, ledger updates once, and objection is not
   repeated.
8. `/dissent`: runs the same agent and evidence validation manually.

## Explicit exclusions

- No standalone website, Framer, React, or 3D scene.
- No RAG, embeddings, vector database, or database.
- No message impersonation or seeded fake Slack users.
- No automated authority over team decisions.
- No generic devil's-advocate response without relevant verified evidence.
- No production privacy or durability claim.

## Primary references

- Slack Bolt Socket Mode: https://docs.slack.dev/tools/bolt-python/concepts/socket-mode/
- Slack public-channel messages: https://docs.slack.dev/reference/events/message.channels/
- Slack reactions: https://docs.slack.dev/reference/events/reaction_added/
- Exa Agent overview: https://exa.ai/docs/reference/agent-api/overview
- Exa create-run API: https://exa.ai/docs/reference/agent-api/create-a-run
