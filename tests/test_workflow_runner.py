from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / ".myteam" / "workflows" / "run_workflow.py"
SPEC = importlib.util.spec_from_file_location("workflow_runner", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
workflow_runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = workflow_runner
SPEC.loader.exec_module(workflow_runner)


class WorkflowRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        self.skill_root = self.repo_root / ".myteam" / "workflows"
        (self.skill_root / "definitions").mkdir(parents=True, exist_ok=True)
        (self.repo_root / ".myteam" / "haiku").mkdir(parents=True, exist_ok=True)
        (self.repo_root / ".myteam" / "free_verse").mkdir(parents=True, exist_ok=True)

        (self.skill_root / "definitions" / "poems.yaml").write_text(
            "name: poems\n"
            "description: Test workflow.\n"
            "roles:\n"
            "  - haiku\n"
            "  - free_verse\n",
            encoding="utf-8",
        )
        (self.repo_root / ".myteam" / "haiku" / "role.md").write_text(
            "---\nname: haiku\ndescription: write a haiku\n---\nWrite a haiku.\n",
            encoding="utf-8",
        )
        (self.repo_root / ".myteam" / "free_verse" / "role.md").write_text(
            "---\nname: free_verse\ndescription: write free verse\n---\nWrite free verse.\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_list_workflows_reads_yaml_definition(self) -> None:
        runtime = workflow_runner.WorkflowRuntime(
            self.skill_root,
            runner=self.fake_runner([]),
            prompt_fn=self.prompt_responses(),
        )

        workflows = runtime.list_workflows()

        self.assertEqual(1, len(workflows))
        self.assertEqual("poems", workflows[0].name)
        self.assertEqual(["haiku", "free_verse"], workflows[0].roles)

    def test_start_runs_only_first_step(self) -> None:
        runtime = workflow_runner.WorkflowRuntime(
            self.skill_root,
            runner=self.fake_runner([workflow_runner.SessionResult(exit_code=0)]),
            prompt_fn=self.prompt_responses("y", "winter haiku"),
        )

        state = runtime.start("poems", "write about winter")

        self.assertEqual(1, state["current_step_index"])
        self.assertEqual(1, len(state["steps"]))
        self.assertEqual("haiku", state["steps"][0]["role"])
        self.assertEqual("winter haiku", state["steps"][0]["summary"])

    def test_next_advances_to_second_step_and_uses_updated_prompt(self) -> None:
        prompts: list[str] = []

        def runner(command: list[str], cwd: Path) -> workflow_runner.SessionResult:
            prompts.append(command[-1])
            return workflow_runner.SessionResult(exit_code=0)

        runtime = workflow_runner.WorkflowRuntime(
            self.skill_root,
            runner=runner,
            prompt_fn=self.prompt_responses("y", "haiku output", "y", "free verse output"),
        )
        started = runtime.start("poems", "winter")

        final_state = runtime.continue_run(started["run_id"], "late winter")

        self.assertEqual(2, final_state["current_step_index"])
        self.assertEqual(2, len(final_state["steps"]))
        self.assertIn("Current user prompt:\nlate winter", prompts[1])
        self.assertIn("haiku:\nhaiku output", prompts[1])

    def test_missing_role_raises_error(self) -> None:
        (self.skill_root / "definitions" / "broken.yaml").write_text(
            "name: broken\nroles:\n  - missing_role\n",
            encoding="utf-8",
        )
        runtime = workflow_runner.WorkflowRuntime(
            self.skill_root,
            runner=self.fake_runner([]),
            prompt_fn=self.prompt_responses(),
        )

        with self.assertRaises(workflow_runner.WorkflowError):
            runtime.start("broken", "test prompt")

    def test_failed_step_is_recorded_without_advancing(self) -> None:
        def runner(command: list[str], cwd: Path) -> workflow_runner.SessionResult:
            return workflow_runner.SessionResult(exit_code=1, stderr="codex failed")

        runtime = workflow_runner.WorkflowRuntime(
            self.skill_root,
            runner=runner,
            prompt_fn=self.prompt_responses(),
        )

        state = runtime.start("poems", "winter")

        self.assertEqual(0, state["current_step_index"])
        self.assertEqual("failed", state["steps"][0]["status"])
        self.assertEqual(1, state["steps"][0]["exit_code"])

    def test_step_can_exit_without_marking_complete(self) -> None:
        runtime = workflow_runner.WorkflowRuntime(
            self.skill_root,
            runner=self.fake_runner([workflow_runner.SessionResult(exit_code=0)]),
            prompt_fn=self.prompt_responses("n"),
        )

        state = runtime.start("poems", "winter")

        self.assertEqual(0, state["current_step_index"])
        self.assertEqual("cancelled", state["steps"][0]["status"])

    def test_interactive_chain_can_continue_into_next_role(self) -> None:
        prompts: list[str] = []

        def runner(command: list[str], cwd: Path) -> workflow_runner.SessionResult:
            prompts.append(command[-1])
            return workflow_runner.SessionResult(exit_code=0)

        runtime = workflow_runner.WorkflowRuntime(
            self.skill_root,
            runner=runner,
            prompt_fn=self.prompt_responses(
                "y",
                "haiku summary",
                "y",
                "",
                "y",
                "free verse summary",
            ),
        )

        state = runtime.start("poems", "spring")
        final_state = runtime.run_interactive_chain(state)

        self.assertEqual(2, final_state["current_step_index"])
        self.assertEqual(2, len(final_state["steps"]))
        self.assertIn("haiku:\nhaiku summary", prompts[1])

    @staticmethod
    def fake_runner(results: list[workflow_runner.SessionResult]):
        index = {"value": 0}

        def runner(command: list[str], cwd: Path) -> workflow_runner.SessionResult:
            current = index["value"]
            index["value"] += 1
            if current < len(results):
                return results[current]
            return workflow_runner.SessionResult(exit_code=0)

        return runner

    @staticmethod
    def prompt_responses(*responses: str):
        answers = iter(responses)

        def prompt(_: str) -> str:
            try:
                return next(answers)
            except StopIteration:
                return ""

        return prompt


if __name__ == "__main__":
    unittest.main()
