# RecallGuard Two-Person Handoff

## Immediate objective

Ship one trustworthy vertical slice before September 12, 2026 at 16:30 CEST:

~~~text
four coding-agent events
→ parallel Code, Test, and History scouts
→ deterministic evidence gate
→ original mech-style browser command center
→ harmful BLOCK and safe PASS
~~~

Implementation contract and acceptance tests are in 'plan.md'. Read it before editing.

## Repository

~~~text
https://github.com/Rahul2899/recallguard
~~~

Visibility starts private. Repository owner invites second builder:

~~~text
GitHub repository → Settings → Collaborators → Add people
~~~

Never paste a personal access token or API key into GitHub, chat, source, fixtures, or screenshots.

## Start commands

Person A:

~~~bash
git clone https://github.com/Rahul2899/recallguard.git
cd recallguard
git checkout -b person-a/core
~~~

Person B:

~~~bash
git clone https://github.com/Rahul2899/recallguard.git
cd recallguard
git checkout -b person-b/frontend
~~~

## Ownership

Person A:

- 'requirements.txt'
- 'recallguard.py'
- 'agents_runtime.py'
- 'examples/*'
- 'tests/test_recallguard.py'
- main integration

Person B:

- 'server.py'
- 'web/*'
- 'tests/test_server.py'
- 'README.md'
- demo narration and submission copy

Frozen after work begins: 'plan.md', 'handoff.md'. No same-file concurrent edits.

## Frozen interface

Person B develops against the response JSON in 'plan.md'. Person A preserves:

~~~python
run_review(scenario: str, root: Path = Path(".")) -> dict
~~~

Allowed scenarios: 'harmful', 'inferred', 'safe'.

Required response keys:

~~~text
scenario
verdict
headline
agents
scouts
findings
~~~

## Integration

1. Person A merges core first.
2. Person B rebases onto 'origin/main'.
3. Person B deletes temporary sample response and imports 'run_review'.
4. Both run the full test suite.
5. Person B opens frontend pull request.
6. Person A reviews and merges.

Only Person A merges to 'main'. Never rewrite 'main'.

## Trust rules

- Only active, verified, incident-and-test-backed lessons block.
- Inferred or incomplete lessons produce UNKNOWN.
- Model output never selects verdict.
- Human owns merge authority.
- RecallGuard never changes source.

## Hard stop

Thirty minutes before submission:

- stop feature work;
- run clean-clone tests;
- record harmful and safe runs;
- submit repository and video.

If behind, cut OpenAI prose and animation first. Never cut deterministic verdicts, evidence, tests, or submission time.
