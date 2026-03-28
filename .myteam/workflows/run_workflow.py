#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml


PromptFn = Callable[[str], str]
RunCommand = Callable[[list[str], Path], "SessionResult"]
AnswerLoader = Callable[[str, int], str]


class WorkflowError(RuntimeError):
    """Raised when workflow definitions or runtime state are invalid."""


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    description: str
    roles: list[str]


@dataclass(frozen=True)
class SessionResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""


class WorkflowRuntime:
    def __init__(
        self,
        skill_root: Path,
        *,
        codex_bin: str = "codex",
        runner: RunCommand | None = None,
        prompt_fn: PromptFn | None = None,
        answer_loader: AnswerLoader | None = None,
    ) -> None:
        self.skill_root = skill_root.resolve()
        self.definitions_dir = self.skill_root / "definitions"
        self.runs_dir = self.skill_root / "runs"
        self.roles_root = self.skill_root.parent
        self.project_root = self.skill_root.parents[1]
        self.codex_bin = codex_bin
        self._runner = runner or self._default_runner
        self._prompt_fn = prompt_fn or input
        self._answer_loader = answer_loader or self._default_answer_loader

    def list_workflows(self) -> list[WorkflowDefinition]:
        if not self.definitions_dir.exists():
            return []

        workflows: list[WorkflowDefinition] = []
        for definition_path in sorted(self.definitions_dir.glob("*.yaml")):
            workflows.append(self._parse_definition(definition_path))
        return workflows

    def load_workflow(self, name: str) -> WorkflowDefinition:
        definition_path = self.definitions_dir / f"{name}.yaml"
        if not definition_path.exists():
            raise WorkflowError(
                f"Workflow '{name}' not found at {definition_path}. "
                "Create a YAML file under .myteam/workflows/definitions/."
            )
        return self._parse_definition(definition_path)

    def start(self, workflow_name: str, prompt: str) -> dict[str, Any]:
        workflow = self.load_workflow(workflow_name)
        if not prompt.strip():
            raise WorkflowError("Prompt must not be empty.")

        run_id = self._new_run_id(workflow.name)
        state = {
            "run_id": run_id,
            "workflow_name": workflow.name,
            "workflow_description": workflow.description,
            "roles": workflow.roles,
            "original_prompt": prompt,
            "current_prompt": prompt,
            "current_step_index": 0,
            "steps": [],
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        self._write_state(state)
        return self.run_next_step(run_id)

    def continue_run(self, run_id: str, prompt_override: str | None = None) -> dict[str, Any]:
        state = self._read_state(run_id)
        if prompt_override is not None:
            if not prompt_override.strip():
                raise WorkflowError("Prompt override must not be empty.")
            state["current_prompt"] = prompt_override

        if self.is_complete(state):
            raise WorkflowError(f"Run '{run_id}' is already complete.")

        self._write_state(state)
        return self.run_next_step(run_id)

    def run_next_step(self, run_id: str) -> dict[str, Any]:
        state = self._read_state(run_id)
        workflow = WorkflowDefinition(
            name=state["workflow_name"],
            description=state["workflow_description"],
            roles=list(state["roles"]),
        )

        step_index = int(state["current_step_index"])
        if step_index >= len(workflow.roles):
            raise WorkflowError(f"Run '{run_id}' is already complete.")

        role_name = workflow.roles[step_index]
        role_instructions = self._read_role_instructions(role_name)
        prompt_text = self._build_codex_prompt(workflow, state, role_name, role_instructions)
        started_unix = int(time.time())
        started_at = utc_now()
        result = self._runner([self.codex_bin, prompt_text], self.project_root)
        finished_at = utc_now()
        completion = self._finalize_step(result, prompt_text, started_unix)

        step_record = {
            "step_index": step_index,
            "role": role_name,
            "prompt": state["current_prompt"],
            "started_at": started_at,
            "finished_at": finished_at,
            "status": completion["status"],
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "final_answer": completion["final_answer"],
        }
        state["steps"].append(step_record)
        if completion["status"] == "succeeded":
            state["current_step_index"] = step_index + 1
        state["updated_at"] = finished_at
        self._write_state(state)
        return state

    def status(self, run_id: str) -> dict[str, Any]:
        return self._read_state(run_id)

    def format_workflow_list(self) -> str:
        workflows = self.list_workflows()
        if not workflows:
            return "No workflow definitions found."

        lines = ["Available workflows:"]
        for workflow in workflows:
            lines.append(
                f"- {workflow.name}: {workflow.description} "
                f"[roles: {', '.join(workflow.roles)}]"
            )
        return "\n".join(lines)

    def format_status(self, state: dict[str, Any]) -> str:
        total_steps = len(state["roles"])
        current_step_index = int(state["current_step_index"])
        remaining = total_steps - current_step_index
        status_line = (
            f"Run {state['run_id']} for workflow '{state['workflow_name']}': "
            f"{current_step_index}/{total_steps} steps completed, {remaining} remaining."
        )

        if self.is_complete(state):
            next_line = "Workflow is complete."
        else:
            next_role = state["roles"][current_step_index]
            next_line = f"Next role: {next_role}"

        lines = [status_line, next_line, "Recorded steps:"]
        if not state["steps"]:
            lines.append("- none")
        else:
            for step in state["steps"]:
                preview = one_line_preview(step_output_text(step))
                line = (
                    f"- step {step['step_index'] + 1} ({step['role']}): "
                    f"{step['status']} exit={step['exit_code']} output={preview}"
                )
                if step["status"] != "succeeded" and step["stderr"].strip():
                    line += f" stderr={one_line_preview(step['stderr'])}"
                lines.append(line)
        return "\n".join(lines)

    def run_interactive_chain(self, state: dict[str, Any]) -> dict[str, Any]:
        current_state = state
        while (
            current_state["steps"]
            and current_state["steps"][-1]["status"] == "succeeded"
            and not self.is_complete(current_state)
        ):
            if not self._confirm_continue_now(current_state["roles"][int(current_state["current_step_index"])]):
                break

            prompt_override = self._prompt_fn(
                "Updated prompt for the next role (optional, press Enter to keep current): "
            ).strip()
            current_state = self.continue_run(
                current_state["run_id"],
                prompt_override if prompt_override else None,
            )

        return current_state

    def is_complete(self, state: dict[str, Any]) -> bool:
        return int(state["current_step_index"]) >= len(state["roles"])

    def _parse_definition(self, definition_path: Path) -> WorkflowDefinition:
        data = yaml.safe_load(definition_path.read_text(encoding="utf-8")) or {}
        name = str(data.get("name") or definition_path.stem).strip()
        description = str(data.get("description") or "").strip()
        roles = data.get("roles")

        if not name:
            raise WorkflowError(f"Workflow file '{definition_path}' is missing a name.")
        if not isinstance(roles, list) or not roles or not all(isinstance(role, str) and role.strip() for role in roles):
            raise WorkflowError(
                f"Workflow '{name}' must declare a non-empty 'roles' list of strings."
            )

        return WorkflowDefinition(
            name=name,
            description=description,
            roles=[role.strip() for role in roles],
        )

    def _finalize_step(self, result: SessionResult, prompt_text: str, started_unix: int) -> dict[str, str]:
        if result.exit_code != 0:
            return {"status": "failed", "final_answer": ""}

        marked_complete = self._confirm_step_complete()
        if not marked_complete:
            return {"status": "cancelled", "final_answer": ""}

        final_answer = self._answer_loader(prompt_text, started_unix).strip()
        return {"status": "succeeded", "final_answer": final_answer}

    def _read_role_instructions(self, role_name: str) -> str:
        role_dir = self.roles_root / role_name
        for candidate in ("role.md", "ROLE.md"):
            path = role_dir / candidate
            if path.exists():
                return strip_frontmatter(path.read_text(encoding="utf-8")).strip()
        raise WorkflowError(f"Role '{role_name}' not found under {role_dir}.")

    def _state_path(self, run_id: str) -> Path:
        return self.runs_dir / f"{run_id}.json"

    def _read_state(self, run_id: str) -> dict[str, Any]:
        state_path = self._state_path(run_id)
        if not state_path.exists():
            raise WorkflowError(f"Run '{run_id}' not found at {state_path}.")
        return json.loads(state_path.read_text(encoding="utf-8"))

    def _write_state(self, state: dict[str, Any]) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        state_path = self._state_path(str(state["run_id"]))
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _build_codex_prompt(
        self,
        workflow: WorkflowDefinition,
        state: dict[str, Any],
        role_name: str,
        role_instructions: str,
    ) -> str:
        prior_outputs = completed_outputs_text(state)
        sections = [
            "You are executing a deterministic myteam workflow step.",
            "",
            f"Workflow: {workflow.name}",
            f"Description: {workflow.description or 'No description provided.'}",
            f"Current role: {role_name}",
            f"Step: {int(state['current_step_index']) + 1} of {len(workflow.roles)}",
            "",
            "Role instructions:",
            role_instructions,
            "",
            "Current user prompt:",
            str(state["current_prompt"]),
            "",
            "Prior successful workflow outputs:",
            prior_outputs,
            "",
            "Return only this role's contribution for the current prompt.",
            "Do not describe the workflow machinery or your hidden reasoning.",
        ]
        return "\n".join(sections).strip()

    def _new_run_id(self, workflow_name: str) -> str:
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        suffix = uuid.uuid4().hex[:8]
        return f"{workflow_name}-{timestamp}-{suffix}"

    def _confirm_step_complete(self) -> bool:
        while True:
            answer = self._prompt_fn("Mark this step complete? [y/N]: ").strip().lower()
            if answer in {"", "n", "no"}:
                return False
            if answer in {"y", "yes"}:
                return True
            print("Please answer 'y' or 'n'.", file=sys.stderr)

    def _confirm_continue_now(self, next_role: str) -> bool:
        while True:
            answer = self._prompt_fn(
                f"Continue now to the next role ({next_role})? [y/N]: "
            ).strip().lower()
            if answer in {"", "n", "no"}:
                return False
            if answer in {"y", "yes"}:
                return True
            print("Please answer 'y' or 'n'.", file=sys.stderr)

    @staticmethod
    def _default_runner(command: list[str], cwd: Path) -> SessionResult:
        completed = subprocess.run(command, cwd=cwd, check=False)
        return SessionResult(exit_code=completed.returncode)

    def _default_answer_loader(self, prompt_text: str, started_unix: int) -> str:
        session_id = self._find_session_id(prompt_text, started_unix)
        if session_id is None:
            return ""

        fd, raw_path = tempfile.mkstemp(prefix="codex-workflow-last-", suffix=".txt")
        os.close(fd)
        output_path = Path(raw_path)
        try:
            command = [
                self.codex_bin,
                "exec",
                "resume",
                "--ephemeral",
                "-o",
                str(output_path),
                session_id,
                "Return only your most recent assistant answer from this session. "
                "Do not add commentary, labels, quotes, or any other text.",
            ]
            completed = subprocess.run(
                command,
                cwd=self.project_root,
                text=True,
                capture_output=True,
                check=False,
            )
            if completed.returncode != 0 or not output_path.exists():
                return ""
            return output_path.read_text(encoding="utf-8").strip()
        finally:
            output_path.unlink(missing_ok=True)

    def _find_session_id(self, prompt_text: str, started_unix: int) -> str | None:
        history_path = Path.home() / ".codex" / "history.jsonl"
        if not history_path.exists():
            return None

        found: str | None = None
        for line in history_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if (
                entry.get("text") == prompt_text
                and isinstance(entry.get("session_id"), str)
                and int(entry.get("ts", 0)) >= max(0, started_unix - 5)
            ):
                found = entry["session_id"]
        return found


def completed_outputs_text(state: dict[str, Any]) -> str:
    outputs = [
        f"{step['role']}:\n{step_output_text(step)}"
        for step in state["steps"]
        if step["status"] == "succeeded" and step_output_text(step)
    ]
    if not outputs:
        return "None yet."
    return "\n\n".join(outputs)


def step_output_text(step: dict[str, Any]) -> str:
    final_answer = str(step.get("final_answer") or "").strip()
    if final_answer:
        return final_answer

    summary = str(step.get("summary") or "").strip()
    if summary:
        return summary

    stdout = str(step.get("stdout") or "").strip()
    if stdout:
        return stdout

    if step.get("status") == "succeeded":
        return "(interactive session completed without a saved summary)"
    return ""


def one_line_preview(text: str, limit: int = 72) -> str:
    content = " ".join(text.split())
    if not content:
        return "(no output)"
    if len(content) <= limit:
        return content
    return f"{content[: limit - 3]}..."


def strip_frontmatter(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text

    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            body = "\n".join(lines[index + 1 :])
            if text.endswith("\n"):
                body += "\n"
            return body
    return text


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run deterministic myteam workflows one role at a time."
    )
    parser.add_argument(
        "--codex-bin",
        default=os.environ.get("CODEX_BIN", "codex"),
        help="Executable used to invoke Codex for each workflow step.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List available workflow definitions.")

    start_parser = subparsers.add_parser("start", help="Start a workflow and run its first step.")
    start_parser.add_argument("workflow", help="Workflow definition name.")
    start_parser.add_argument("--prompt", required=True, help="Prompt for the workflow.")

    next_parser = subparsers.add_parser("next", help="Run the next step for an existing workflow run.")
    next_parser.add_argument("run_id", help="Existing workflow run identifier.")
    next_parser.add_argument(
        "--prompt",
        dest="prompt_override",
        help="Optional replacement prompt to use for the next step.",
    )

    status_parser = subparsers.add_parser("status", help="Inspect workflow run status.")
    status_parser.add_argument("run_id", help="Existing workflow run identifier.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    runtime = WorkflowRuntime(Path(__file__).resolve().parent, codex_bin=args.codex_bin)

    try:
        if args.command == "list":
            print(runtime.format_workflow_list())
            return 0
        if args.command == "start":
            state = runtime.start(args.workflow, args.prompt)
            state = runtime.run_interactive_chain(state)
            print(runtime.format_status(state))
            return 0
        if args.command == "next":
            state = runtime.continue_run(args.run_id, args.prompt_override)
            state = runtime.run_interactive_chain(state)
            print(runtime.format_status(state))
            return 0
        if args.command == "status":
            print(runtime.format_status(runtime.status(args.run_id)))
            return 0
    except WorkflowError as exc:
        print(f"Workflow error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
