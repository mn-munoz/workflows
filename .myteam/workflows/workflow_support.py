from __future__ import annotations

from datetime import datetime, timezone

from workflow_models import WorkflowStepRecord


def step_output_text(step: WorkflowStepRecord | dict[str, object]) -> str:
    if isinstance(step, WorkflowStepRecord):
        final_answer = step.final_answer.strip()
        if final_answer:
            return final_answer

        stdout = step.stdout.strip()
        if stdout:
            return stdout

        if step.status == "succeeded":
            return "(interactive session completed without a saved summary)"
        return ""

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


def completed_outputs_text(steps: list[WorkflowStepRecord]) -> str:
    outputs = [
        "\n".join(
            [
                f"BEGIN UPSTREAM PAYLOAD: {step.role}",
                step_output_text(step),
                f"END UPSTREAM PAYLOAD: {step.role}",
            ]
        )
        for step in steps
        if step.status == "succeeded" and step_output_text(step)
    ]
    if not outputs:
        return "None yet."
    return "\n\n".join(outputs)


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
