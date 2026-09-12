# Counterpoint

Counterpoint is a Slack-native agent with one mandate: notice when a team is
closing a consequential decision without addressing a relevant concern, then
raise one evidence-backed objection.

It does not answer questions. It watches a real multi-person channel, reasons
over the recent discussion and a small record of earlier decisions, speaks at
most once per decision, and yields immediately when a teammate reacts with
thumbs-down. Dismissed objections remain visible in a decision ledger.

## Planning baseline

Implementation intentionally starts after both builders branch from this
commit. Read these files first:

- `docs/superpowers/specs/2026-09-12-counterpoint-design.md`
- `docs/superpowers/plans/2026-09-12-counterpoint-implementation.md`
- `TEAM_SPLIT.md`

The previous RecallGuard project remains recoverable in Git branch
`archive/recallguard-v1`.

## Boundaries

- Slack is the product interface. No separate web or 3D frontend.
- Exa Agent performs semantic judgment and objection writing.
- Deterministic code handles transport, limits, provenance, and human control.
- CopilotKit Intelligence is not required for the core path.
- Demo data must be synthetic and disclosed as such.
