from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class WorkflowError(RuntimeError):
    """Raised when workflow definitions or runtime state are invalid."""


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    description: str
    roles: list[str]

    @classmethod
    def from_mapping(cls, data: dict[str, Any], *, fallback_name: str) -> "WorkflowDefinition":
        name = str(data.get("name") or fallback_name).strip()
        description = str(data.get("description") or "").strip()
        roles = data.get("roles")

        if not name:
            raise WorkflowError("Workflow definitions must include a non-empty name.")
        if not isinstance(roles, list) or not roles or not all(
            isinstance(role, str) and role.strip() for role in roles
        ):
            raise WorkflowError(
                f"Workflow '{name}' must declare a non-empty 'roles' list of strings."
            )

        return cls(
            name=name,
            description=description,
            roles=[role.strip() for role in roles],
        )


@dataclass(frozen=True)
class SessionResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class WorkflowStepRecord:
    step_index: int
    role: str
    prompt: str
    started_at: str
    finished_at: str
    status: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    final_answer: str = ""

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "WorkflowStepRecord":
        return cls(
            step_index=int(data["step_index"]),
            role=str(data["role"]),
            prompt=str(data["prompt"]),
            started_at=str(data["started_at"]),
            finished_at=str(data["finished_at"]),
            status=str(data["status"]),
            exit_code=int(data["exit_code"]),
            stdout=str(data.get("stdout") or ""),
            stderr=str(data.get("stderr") or ""),
            final_answer=str(data.get("final_answer") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkflowRunState:
    run_id: str
    workflow_name: str
    workflow_description: str
    roles: list[str]
    original_prompt: str
    current_prompt: str
    current_step_index: int
    steps: list[WorkflowStepRecord] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "WorkflowRunState":
        return cls(
            run_id=str(data["run_id"]),
            workflow_name=str(data["workflow_name"]),
            workflow_description=str(data.get("workflow_description") or ""),
            roles=[str(role) for role in data["roles"]],
            original_prompt=str(data["original_prompt"]),
            current_prompt=str(data["current_prompt"]),
            current_step_index=int(data["current_step_index"]),
            steps=[WorkflowStepRecord.from_mapping(step) for step in data.get("steps", [])],
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
        )

    @classmethod
    def new(
        cls,
        *,
        run_id: str,
        workflow: WorkflowDefinition,
        prompt: str,
        created_at: str,
    ) -> "WorkflowRunState":
        return cls(
            run_id=run_id,
            workflow_name=workflow.name,
            workflow_description=workflow.description,
            roles=list(workflow.roles),
            original_prompt=prompt,
            current_prompt=prompt,
            current_step_index=0,
            steps=[],
            created_at=created_at,
            updated_at=created_at,
        )

    @property
    def total_steps(self) -> int:
        return len(self.roles)

    @property
    def is_complete(self) -> bool:
        return self.current_step_index >= self.total_steps

    @property
    def next_role(self) -> str | None:
        if self.is_complete:
            return None
        return self.roles[self.current_step_index]

    def workflow_definition(self) -> WorkflowDefinition:
        return WorkflowDefinition(
            name=self.workflow_name,
            description=self.workflow_description,
            roles=list(self.roles),
        )

    def with_prompt(self, prompt: str) -> "WorkflowRunState":
        return WorkflowRunState(
            run_id=self.run_id,
            workflow_name=self.workflow_name,
            workflow_description=self.workflow_description,
            roles=list(self.roles),
            original_prompt=self.original_prompt,
            current_prompt=prompt,
            current_step_index=self.current_step_index,
            steps=list(self.steps),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    def append_step(
        self,
        step: WorkflowStepRecord,
        *,
        next_step_index: int | None = None,
        updated_at: str,
    ) -> "WorkflowRunState":
        return WorkflowRunState(
            run_id=self.run_id,
            workflow_name=self.workflow_name,
            workflow_description=self.workflow_description,
            roles=list(self.roles),
            original_prompt=self.original_prompt,
            current_prompt=self.current_prompt,
            current_step_index=self.current_step_index if next_step_index is None else next_step_index,
            steps=[*self.steps, step],
            created_at=self.created_at,
            updated_at=updated_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_name": self.workflow_name,
            "workflow_description": self.workflow_description,
            "roles": list(self.roles),
            "original_prompt": self.original_prompt,
            "current_prompt": self.current_prompt,
            "current_step_index": self.current_step_index,
            "steps": [step.to_dict() for step in self.steps],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
