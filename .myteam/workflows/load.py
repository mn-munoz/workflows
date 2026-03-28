#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import yaml

from myteam.utils import print_instructions, get_myteam_root, list_roles, list_skills, list_tools


def print_workflows(base: Path) -> None:
    definitions_dir = base / "definitions"
    if not definitions_dir.exists():
        return

    workflows: list[tuple[str, str]] = []
    for definition in sorted(definitions_dir.glob("*.yaml")):
        data = yaml.safe_load(definition.read_text(encoding="utf-8")) or {}
        name = str(data.get("name") or definition.stem)
        description = str(data.get("description") or "")
        workflows.append((name, description))

    if not workflows:
        return

    print()
    print(" Available Workflows ".center(30, "*"))
    for name, description in workflows:
        print(f" {name} ".center(30, "-"))
        if description:
            print(description)
    print()


def main() -> int:
    base = Path(__file__).resolve().parent  # .myteam/<role>
    print_instructions(base)
    myteam = get_myteam_root(base)
    list_roles(base, myteam, [])
    list_skills(base, myteam, [])
    print_workflows(base)
    list_tools(base, myteam, [])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
