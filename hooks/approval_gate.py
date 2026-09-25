"""PreToolUse hook (Edit|Write|MultiEdit|NotebookEdit) -- the design->code checkpoint, enforced.

While a breakdown is awaiting approval, source edits are denied. Planning artifacts stay
writable: anything under .claude/, the SDD directory, and Markdown files.

Claude may not approve its own plan: an edit to task_state.json that flips `approved`
from false to true is denied. Only the user's own "approved" message does that, via
prompt_submit.py.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import Project, emit, project_root, read_json, read_payload, run_safely  # noqa: E402

APPROVED_TRUE = re.compile(r'"approved"\s*:\s*true')


def deny(reason: str) -> None:
    emit({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    })


def target_path(project: Project, tool_input: dict[str, Any]) -> Path | None:
    raw = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path")
    if not raw:
        return None
    path = Path(str(raw))
    if not path.is_absolute():
        path = project.root / path
    try:
        return path.resolve()
    except OSError:
        return path


def is_self_approval(project: Project, tool_name: str, tool_input: dict[str, Any]) -> bool:
    if read_json(project.task_state).get("approved") is True:
        return False
    if tool_name == "Write":
        try:
            return json.loads(str(tool_input.get("content", ""))).get("approved") is True
        except (json.JSONDecodeError, ValueError, AttributeError):
            return bool(APPROVED_TRUE.search(str(tool_input.get("content", ""))))
    edits = tool_input.get("edits") if tool_name == "MultiEdit" else [tool_input]
    for edit in edits or []:
        if not isinstance(edit, dict):
            continue
        if APPROVED_TRUE.search(str(edit.get("new_string", ""))) and not APPROVED_TRUE.search(
            str(edit.get("old_string", ""))
        ):
            return True
    return False


def is_planning_artifact(project: Project, path: Path, sdd_dir: str) -> bool:
    root = project.root.resolve()
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True  # outside the project: not ours to gate
    parts = rel.parts
    if parts and parts[0] == ".claude":
        return True
    sdd_parts = Path(sdd_dir).parts
    if sdd_parts and tuple(parts[: len(sdd_parts)]) == tuple(sdd_parts):
        return True
    return path.suffix.lower() in {".md", ".mdx"}


def main() -> None:
    payload = read_payload()
    project = Project(project_root(payload))
    if not project.enabled:
        return
    config = project.config()
    if config.get("approval_gate") is False:
        return

    tool_name = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input") or {}
    path = target_path(project, tool_input)
    if path is None:
        return

    if path == project.task_state.resolve() and is_self_approval(project, tool_name, tool_input):
        deny(
            "Only the user can approve the plan. Stop and ask them to review the task list and "
            "reply 'approved'; a hook sets the flag from their message."
        )
        return

    state = read_json(project.task_state)
    if not state.get("tasks") or state.get("approved") is True:
        return
    if is_planning_artifact(project, path, str(config.get("sdd_path") or "docs/sdd")):
        return
    deny(
        f"Plan awaiting approval: {len(state['tasks'])} task(s) from {state.get('sdd_path') or 'the SDD'} "
        "have not been approved, so source edits are blocked. Show the user the task list and ask "
        "them to reply 'approved'. (To work outside this plan, the user can run "
        "/takshak:checkpoint clear-plan, or set approval_gate: false in .claude/framework.json.)"
    )


if __name__ == "__main__":
    run_safely(main)
