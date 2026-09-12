# Counterpoint Two-Person Split

Both builders branch from the planning-baseline commit on `origin/main`. The
design and implementation plan are frozen during parallel work.

## Plan A — repository owner

Branch: `owner/plan-a-slack`

Owns:

- `app.py`
- `slack_app_manifest.yaml`
- `.env.example`
- `requirements.txt`
- `tests/test_app.py`
- final `README.md` update after integration
- Slack developer-console setup and live workspace verification

Delivers Slack Socket Mode ingestion, one serialized worker, channel filtering,
agent-call orchestration, native evidence card, thumbs-down dismissal, ledger,
and `/dissent`.

Plan A must not create or edit Plan B files. Develop against an injected
`analyze` callable returning the frozen decision dictionary. The production
entry point imports `counterpoint_agent.analyze_window` only when the process
starts, allowing Plan A tests to run before Plan B merges.

## Plan B — teammate

Branch: `teammate/plan-b-agent`

Owns:

- `counterpoint_agent.py`
- `team_memory.json`
- `demo_transcripts.json`
- `tests/test_counterpoint_agent.py`

Delivers the real Exa Agent call, asynchronous polling, strict output schema,
memory validation, evidence validation, canonical abstention behavior, and the
three demo transcripts.

Plan B must not create or edit Plan A files or frozen planning documents.

## Frozen interface

~~~python
def analyze_window(messages: list[dict[str, str]]) -> dict[str, object]:
    """Return exactly the decision shape in the design; provider failures abstain."""
~~~

Plan A calls it with up to 15 ordered human messages. Plan B owns all provider
details. No Slack object crosses this boundary.

## Start commands

Repository owner:

~~~bash
git fetch origin
git switch -c owner/plan-a-slack origin/main
~~~

Teammate, new clone:

~~~bash
git clone https://github.com/Rahul2899/counterpoint-slack.git
cd counterpoint-slack
git switch -c teammate/plan-b-agent
~~~

Teammate, existing old-name clone:

~~~bash
git remote set-url origin https://github.com/Rahul2899/counterpoint-slack.git
git fetch origin
git switch -c teammate/plan-b-agent origin/main
~~~

## Pull requests and merge order

1. Teammate opens `Plan B: Exa decision agent` into `main`.
2. Repository owner reviews contract and tests, then merges Plan B.
3. Repository owner rebases Plan A onto updated `origin/main`.
4. Repository owner runs the full unit suite and one live Slack rehearsal.
5. Repository owner opens and merges `Plan A: Slack runtime and integration`.

Only the repository owner changes Slack credentials or merges to `main`.

## Shared completion command

~~~bash
python3 -m unittest discover -s tests -v
~~~

No PR may contain `.env`, tokens, copied API-key values, real private Slack
messages, or screenshots showing secrets.

