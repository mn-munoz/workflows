from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from workflow_models import (
    SessionResult,
    WorkflowDefinition,
    WorkflowError,
    WorkflowRunState,
    WorkflowStepRecord,
)
from workflow_storage import (
    RoleInstructionRepository,
    WorkflowDefinitionRepository,
    WorkflowRunStateStore,
)
from workflow_support import completed_outputs_text, one_line_preview, step_output_text, utc_now


PromptFn = Callable[[str], str]
RunCommand = Callable[[list[str], Path], SessionResult]
AnswerLoader = Callable[[str, int], str]
TimestampFn = Callable[[], str]
RunIdFactory = Callable[[str], str]


class WorkflowPromptBuilder:
    def build(
        self,
        workflow: WorkflowDefinition,
        state: WorkflowRunState,
        role_name: str,
        role_instructions: str,
    ) -> str:
        sections = [
            "You are executing a deterministic myteam workflow step.",
            "",
            f"Workflow: {workflow.name}",
            f"Description: {workflow.description or 'No description provided.'}",
            f"Workflow run id: {state.run_id}",
            f"Current role: {role_name}",
            f"Step: {state.current_step_index + 1} of {len(workflow.roles)}",
            "",
            "Role instructions:",
            role_instructions,
            "",
            "Current user prompt:",
            state.current_prompt,
            "",
            "Prior successful workflow handoff payloads:",
            completed_outputs_text(state.steps),
            "",
            "Treat prior handoff payloads as exact upstream outputs.",
            "Preserve structured field names, file paths, and run ids from upstream payloads.",
            "Return only this role's contribution for the current prompt.",
            "Do not describe the workflow machinery or your hidden reasoning.",
            "When you are done with the task, state that you are finished and wait for the user to mark the step complete.",
        ]
        return "\n".join(sections).strip()


class WorkflowStatusFormatter:
    def format_workflow_list(self, workflows: list[WorkflowDefinition]) -> str:
        if not workflows:
            return "No workflow definitions found."

        lines = ["Available workflows:"]
        for workflow in workflows:
            lines.append(
                f"- {workflow.name}: {workflow.description} "
                f"[roles: {', '.join(workflow.roles)}]"
            )
        return "\n".join(lines)

    def format_status(self, state: WorkflowRunState) -> str:
        remaining = state.total_steps - state.current_step_index
        status_line = (
            f"Run {state.run_id} for workflow '{state.workflow_name}': "
            f"{state.current_step_index}/{state.total_steps} steps completed, {remaining} remaining."
        )

        if state.is_complete:
            next_line = "Workflow is complete."
        else:
            next_line = f"Next role: {state.next_role}"

        lines = [status_line, next_line, "Recorded steps:"]
        if not state.steps:
            lines.append("- none")
        else:
            for step in state.steps:
                preview = one_line_preview(step_output_text(step))
                line = (
                    f"- step {step.step_index + 1} ({step.role}): "
                    f"{step.status} exit={step.exit_code} output={preview}"
                )
                if step.status != "succeeded" and step.stderr.strip():
                    line += f" stderr={one_line_preview(step.stderr)}"
                lines.append(line)
        return "\n".join(lines)


class WorkflowRuntime:
    def __init__(
        self,
        skill_root: Path,
        *,
        codex_bin: str = "codex",
        runner: RunCommand | None = None,
        prompt_fn: PromptFn | None = None,
        answer_loader: AnswerLoader | None = None,
        prompt_builder: WorkflowPromptBuilder | None = None,
        status_formatter: WorkflowStatusFormatter | None = None,
        timestamp_fn: TimestampFn | None = None,
        run_id_factory: RunIdFactory | None = None,
    ) -> None:
        self.skill_root = skill_root.resolve()
        self.roles_root = self.skill_root.parent
        self.project_root = self.skill_root.parents[1]
        self.codex_bin = codex_bin
        self._runner = runner or self._default_runner
        self._prompt_fn = prompt_fn or input
        self._answer_loader = answer_loader or self._default_answer_loader
        self._prompt_builder = prompt_builder or WorkflowPromptBuilder()
        self._status_formatter = status_formatter or WorkflowStatusFormatter()
        self._timestamp_fn = timestamp_fn or utc_now
        self._run_id_factory = run_id_factory or default_run_id_factory
        self._definitions = WorkflowDefinitionRepository(self.skill_root / "definitions")
        self._roles = RoleInstructionRepository(self.roles_root)
        self._states = WorkflowRunStateStore(self.skill_root / "runs")

    def list_workflows(self) -> list[WorkflowDefinition]:
        return self._definitions.list()

    def load_workflow(self, name: str) -> WorkflowDefinition:
        return self._definitions.get(name)

    def start(self, workflow_name: str, prompt: str) -> dict[str, object]:
        workflow = self.load_workflow(workflow_name)
        if not prompt.strip():
            raise WorkflowError("Prompt must not be empty.")

        created_at = self._timestamp_fn()
        state = WorkflowRunState.new(
            run_id=self._run_id_factory(workflow.name),
            workflow=workflow,
            prompt=prompt,
            created_at=created_at,
        )
        self._states.save(state)
        return self.run_next_step(state.run_id)

    def continue_run(self, run_id: str, prompt_override: str | None = None) -> dict[str, object]:
        state = self._states.load(run_id)
        if prompt_override is not None:
            if not prompt_override.strip():
                raise WorkflowError("Prompt override must not be empty.")
            state = state.with_prompt(prompt_override)

        if state.is_complete:
            raise WorkflowError(f"Run '{run_id}' is already complete.")

        self._states.save(state)
        return self.run_next_step(run_id)

    def run_next_step(self, run_id: str) -> dict[str, object]:
        state = self._states.load(run_id)
        workflow = state.workflow_definition()

        if state.is_complete:
            raise WorkflowError(f"Run '{run_id}' is already complete.")

        role_name = state.next_role
        assert role_name is not None
        role_instructions = self._roles.get(role_name)
        prompt_text = self._prompt_builder.build(workflow, state, role_name, role_instructions)
        started_unix = int(time.time())
        started_at = self._timestamp_fn()
        result = self._runner([self.codex_bin, prompt_text], self.project_root)
        finished_at = self._timestamp_fn()
        completion = self._finalize_step(result, prompt_text, started_unix)

        step_record = WorkflowStepRecord(
            step_index=state.current_step_index,
            role=role_name,
            prompt=state.current_prompt,
            started_at=started_at,
            finished_at=finished_at,
            status=completion["status"],
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            final_answer=completion["final_answer"],
        )
        next_step_index = None
        if completion["status"] == "succeeded":
            next_step_index = state.current_step_index + 1

        updated_state = state.append_step(
            step_record,
            next_step_index=next_step_index,
            updated_at=finished_at,
        )
        self._states.save(updated_state)
        return updated_state.to_dict()

    def status(self, run_id: str) -> dict[str, object]:
        return self._states.load(run_id).to_dict()

    def format_workflow_list(self) -> str:
        return self._status_formatter.format_workflow_list(self.list_workflows())

    def format_status(self, state: dict[str, object]) -> str:
        return self._status_formatter.format_status(WorkflowRunState.from_mapping(state))

    def run_interactive_chain(self, state: dict[str, object]) -> dict[str, object]:
        current_state = WorkflowRunState.from_mapping(state)
        while (
            current_state.steps
            and current_state.steps[-1].status == "succeeded"
            and not current_state.is_complete
        ):
            next_role = current_state.next_role
            assert next_role is not None
            if not self._confirm_continue_now(next_role):
                break

            prompt_override = self._prompt_fn(
                "Updated prompt for the next role (optional, press Enter to keep current): "
            ).strip()
            current_state = WorkflowRunState.from_mapping(
                self.continue_run(
                    current_state.run_id,
                    prompt_override if prompt_override else None,
                )
            )

        return current_state.to_dict()

    def is_complete(self, state: dict[str, object]) -> bool:
        return WorkflowRunState.from_mapping(state).is_complete

    def _finalize_step(self, result: SessionResult, prompt_text: str, started_unix: int) -> dict[str, str]:
        if result.exit_code != 0:
            return {"status": "failed", "final_answer": ""}

        marked_complete = self._confirm_step_complete()
        if not marked_complete:
            return {"status": "cancelled", "final_answer": ""}

        final_answer = self._answer_loader(prompt_text, started_unix).strip()
        return {"status": "succeeded", "final_answer": final_answer}

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


def default_run_id_factory(workflow_name: str) -> str:
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:8]
    return f"{workflow_name}-{timestamp}-{suffix}"
