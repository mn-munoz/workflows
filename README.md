# Codex CLI Workflow

This repo now includes a repo-local `myteam` workflow skill that lets you run
roles in a fixed order while keeping the user in the loop between steps.

## What It Does

The workflow skill lives in `.myteam/workflows`. It provides:

- YAML workflow definitions with deterministic role order
- A Python runner that opens an interactive `codex` session for each role
- Persistent run state so you can pause, inspect, and continue

The default interaction loop is:

1. Start a workflow with a prompt.
2. The runner opens a real `codex` CLI session for the next role.
3. You interact with Codex directly in that session.
4. When you exit Codex, the runner asks whether to mark the step complete.
5. If completed, the runner saves the final assistant answer from that session.
6. The runner can immediately continue into the next role in the same command.
7. If you stop between roles, run `next` later to resume from the saved run.

## Commands

Use the workflow tool directly:

```bash
python .myteam/workflows/run_workflow.py list
python .myteam/workflows/run_workflow.py start poems --prompt "winter"
python .myteam/workflows/run_workflow.py status <run_id>
python .myteam/workflows/run_workflow.py next <run_id>
python .myteam/workflows/run_workflow.py next <run_id> --prompt "late winter in the mountains"
```

You can also load the skill instructions with:

```bash
myteam get skill workflows
```

## Workflow YAML Format

Workflow files live in `.myteam/workflows/definitions/` and use a small schema:

```yaml
name: poems
description: Write two winter-themed poems in a fixed order.
roles:
  - haiku
  - free_verse
```

## Included Example

The repo ships with a `poems` workflow that runs:

1. `haiku`
2. `free_verse`

That mirrors the existing `cdx` example, but now the order is encoded in data
and each step pauses so the user can interact before the next role runs.
