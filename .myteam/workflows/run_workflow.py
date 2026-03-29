#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from workflow_models import SessionResult, WorkflowDefinition, WorkflowError
from workflow_runtime import WorkflowRuntime
from workflow_support import completed_outputs_text, one_line_preview, step_output_text, strip_frontmatter, utc_now


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
