from __future__ import annotations

import json
from pathlib import Path

import yaml

from workflow_models import WorkflowDefinition, WorkflowError, WorkflowRunState
from workflow_support import strip_frontmatter


class WorkflowDefinitionRepository:
    def __init__(self, definitions_dir: Path) -> None:
        self.definitions_dir = definitions_dir

    def list(self) -> list[WorkflowDefinition]:
        if not self.definitions_dir.exists():
            return []

        return [
            self._load_definition(definition_path)
            for definition_path in sorted(self.definitions_dir.glob("*.yaml"))
        ]

    def get(self, name: str) -> WorkflowDefinition:
        definition_path = self.definitions_dir / f"{name}.yaml"
        if not definition_path.exists():
            raise WorkflowError(
                f"Workflow '{name}' not found at {definition_path}. "
                "Create a YAML file under .myteam/workflows/definitions/."
            )
        return self._load_definition(definition_path)

    def _load_definition(self, definition_path: Path) -> WorkflowDefinition:
        data = yaml.safe_load(definition_path.read_text(encoding="utf-8")) or {}
        return WorkflowDefinition.from_mapping(data, fallback_name=definition_path.stem)


class RoleInstructionRepository:
    def __init__(self, roles_root: Path) -> None:
        self.roles_root = roles_root

    def get(self, role_name: str) -> str:
        role_dir = self.roles_root / role_name
        for candidate in ("role.md", "ROLE.md"):
            path = role_dir / candidate
            if path.exists():
                return strip_frontmatter(path.read_text(encoding="utf-8")).strip()
        raise WorkflowError(f"Role '{role_name}' not found under {role_dir}.")


class WorkflowRunStateStore:
    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = runs_dir

    def path_for(self, run_id: str) -> Path:
        return self.runs_dir / f"{run_id}.json"

    def load(self, run_id: str) -> WorkflowRunState:
        state_path = self.path_for(run_id)
        if not state_path.exists():
            raise WorkflowError(f"Run '{run_id}' not found at {state_path}.")
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return WorkflowRunState.from_mapping(data)

    def save(self, state: WorkflowRunState) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        state_path = self.path_for(state.run_id)
        state_path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
