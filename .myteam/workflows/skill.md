---
name: workflows
description: Run deterministic multi-role workflows with pause points between roles.
---

Use this skill when the user wants a deterministic, ordered workflow that hands
work to specific `myteam` roles one step at a time.

The workflow contract in this repo is:

- workflow order is defined in YAML files under `definitions/`
- each invocation runs exactly one role step
- each step opens a real interactive `codex` CLI session
- after the session exits, the user decides whether that step is complete
- after a completed step, the runner can immediately launch the next role
- role execution is delegated through the Python runner, not improvised manually

Primary tool:

- `python .myteam/workflows/run_workflow.py list`
- `python .myteam/workflows/run_workflow.py start <workflow> --prompt "<text>"`
- `python .myteam/workflows/run_workflow.py status <run_id>`
- `python .myteam/workflows/run_workflow.py next <run_id> [--prompt "<updated text>"]`

When using this skill:

- prefer the configured YAML workflow over ad hoc role ordering
- preserve prior step summaries in the workflow context
- stop after each role so the user stays in the loop
- use `status` to report progress before advancing if the user wants an update
