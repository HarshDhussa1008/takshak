"""PostToolUse hook (Edit|Write|MultiEdit) -- architecture rules, at the moment of the edit.

Runs the file-level fitness rules from .claude/fitness.json on the file just edited and tells
Claude about any new violation right away, in the same loop as lint and type errors, instead
of a reviewer finding it a week later. Silent when there are no rules or no violations.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _fitness as fitness  # noqa: E402
from _common import Project, emit_context, project_root, read_payload, run_safely  # noqa: E402
from _reconcile import relative  # noqa: E402


def main() -> None:
    payload = read_payload()
    project = Project(project_root(payload))
    if not project.enabled or project.config().get("fitness_gate") is False:
        return
    if not fitness.load_rules(project)["rules"]:
        return
    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("path")
    rel = relative(project, raw) if raw else None
    if not rel or Path(rel).suffix.lower() not in fitness.CODE_SUFFIXES:
        return
    violations = fitness.check(project, only={rel})
    if not violations:
        return
    lines = [f"- [{v['rule']}] {v['file']}: {v['detail']}" + (f" ({v['description']})" if v["description"] else "") for v in violations[:8]]
    emit_context("PostToolUse", "Architecture rules (.claude/fitness.json) are violated by this edit -- fix the dependency, "
                 "do not edit the rule to make it pass:\n" + "\n".join(lines))


if __name__ == "__main__":
    run_safely(main)
