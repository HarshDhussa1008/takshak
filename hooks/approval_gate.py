"""PreToolUse hook (Edit|Write|MultiEdit|NotebookEdit|Bash) -- the design->code checkpoint, enforced.

Two checkpoints, both for source edits (planning artifacts stay writable: anything under
.claude/, the SDD directory, and Markdown files):

1. While a breakdown is awaiting approval, source edits are denied.
2. Outside an approved plan, a source edit needs an open *work item* (and, with Jira on, a
   ticket linked or deferred on it). That is what stops ad-hoc requests from bypassing the
   pipeline and leaving task state and the repo in a mess. `/takshak:work` opens one.

Bash is covered too, for the clear write patterns (redirects, tee, sed -i, cp/mv, git apply).

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

import _design_change as design_change  # noqa: E402
import _structure as structure  # noqa: E402
import _work as work  # noqa: E402
from _common import Project, emit, project_root, read_json, read_payload, run_safely  # noqa: E402

APPROVED_TRUE = re.compile(r'"approved"\s*:\s*true')
BASH_APPROVED_TRUE = re.compile(r"approved\W{0,8}(true|1)\b", re.IGNORECASE)  # any quoting or language: ['approved']=True, "approved": true, approved: 1
CHANGE_APPROVED = re.compile(r'"status"\s*:\s*"approved"')


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


def is_change_self_approval(project: Project, tool_name: str, tool_input: dict[str, Any]) -> bool:
    """Claude writing "status": "approved" into design_changes.json is approving its own design change."""
    content = str(tool_input.get("content", "")) if tool_name == "Write" else ""
    if tool_name == "Write":
        before = project.state_dir / design_change.STATE_NAME
        old = before.read_text(encoding="utf-8") if before.is_file() else ""
        return len(CHANGE_APPROVED.findall(content)) > len(CHANGE_APPROVED.findall(old))
    edits = tool_input.get("edits") if tool_name == "MultiEdit" else [tool_input]
    return any(
        isinstance(e, dict) and CHANGE_APPROVED.search(str(e.get("new_string", ""))) and not CHANGE_APPROVED.search(str(e.get("old_string", "")))
        for e in edits or []
    )


def is_planning_artifact(project: Project, path: Path, sdd_dir: str) -> bool:
    root = project.root.resolve()
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True  # outside the project: not ours to gate
    parts = rel.parts
    if parts and parts[0] in {".claude", "docs"}:
        return True  # configuration, state and the whole documentation tree are never source
    sdd_parts = Path(sdd_dir).parts
    if sdd_parts and tuple(parts[: len(sdd_parts)]) == tuple(sdd_parts):
        return True
    return path.suffix.lower() in {".md", ".mdx"}


def bash_target(project: Project, command: str) -> tuple[Path | None, str | None]:
    """First project-relative file a Bash command writes -> (path, None); a patch whose files
    cannot be known -> (project root, '<patch>'); nothing relevant -> (None, None)."""
    for raw in work.bash_write_targets(command):
        if raw == "<patch>":
            return project.root.resolve(), raw
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = project.root / path
        try:
            resolved = path.resolve()
            resolved.relative_to(project.root.resolve())
        except (ValueError, OSError):
            continue  # outside the project (/tmp, ~): not ours to gate
        return resolved, raw
    return None, None


def work_item_block(project: Project) -> str | None:
    if not work.gate_enabled(project) or work.plan_covers_edits(project):
        return None
    item = work.active_item(project)
    if item is None:
        return (
            "No work item is open, so this source change is not attributed to anything. Run "
            "/takshak:work first: it sizes the request, opens a work item"
            + (" and a Jira ticket of the right type" if work.jira_on(project) else "")
            + ", and then editing is unblocked. (Bug reports, small tweaks and features all go through it; "
            "turn the check off with work_item_gate: false in .claude/framework.json.)"
        )
    if work.ticket_state(project, item) == "missing":
        return (
            f"Work item {item['id']} ('{item['title']}') has no Jira ticket. Link one "
            f"(`tools/work.py link {item['id']} <KEY>`) or, if Jira is unreachable, defer it "
            f"(`tools/work.py defer {item['id']} --reason ...`) -- /takshak:work does either."
        )
    return None


def main() -> None:
    payload = read_payload()
    project = Project(project_root(payload))
    if not project.enabled:
        return
    config = project.config()
    if config.get("approval_gate") is False and not work.gate_enabled(project):
        return

    tool_name = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input") or {}
    if tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        path, raw = bash_target(project, command)
        if path is None:
            return
        if path == project.task_state.resolve() and (APPROVED_TRUE.search(command) or BASH_APPROVED_TRUE.search(command)) \
                and read_json(project.task_state).get("approved") is not True:
            deny("Only the user can approve the plan. Ask them to reply 'approved'; a hook sets the flag.")
            return
        if path == (project.state_dir / design_change.STATE_NAME).resolve() and CHANGE_APPROVED.search(command):
            deny("Only the user can approve a design change. Ask them to reply 'approved'; a hook records it.")
            return
    else:
        path = target_path(project, tool_input)
        if path is None:
            return
        if path == project.task_state.resolve() and is_self_approval(project, tool_name, tool_input):
            deny(
                "Only the user can approve the plan. Stop and ask them to review the task list and "
                "reply 'approved'; a hook sets the flag from their message."
            )
            return
        if path == (project.state_dir / design_change.STATE_NAME).resolve() and is_change_self_approval(project, tool_name, tool_input):
            deny("Only the user can approve a design change. Ask them to review the delta and reply 'approved'; "
                 "a hook records it from their message.")
            return

    if tool_name == "Write" and config.get("structure_gate") is not False:
        if misplaced := structure.write_violation(project, path, tool_name, tool_input):
            deny(f"Document filed in the wrong place: {misplaced}")
            return

    sdd_dir = str(config.get("sdd_path") or "docs/sdd")
    if raw_is_patch(path, project, tool_name, tool_input):
        planning = False
    else:
        planning = is_planning_artifact(project, path, sdd_dir)
    if planning:
        return

    if pending := design_change.blocking(project):
        first = pending[0]
        verb = {
            "open": "is being analysed -- finish the analysis, write the ADR and run `design_change.py propose`",
            "proposed": "is waiting for the user's approval",
            "approved": "is approved but not yet applied -- update the SDD, tasks and tests, then run `design_change.py apply`",
        }[first["status"]]
        deny(
            f"Design change {first['id']} ('{first['title']}') {verb}, so source edits are blocked until it is "
            "applied: the design is being corrected before more code is built on it. Planning artifacts "
            "(SDD, ADRs, docs) stay writable. Use /takshak:redesign."
        )
        return

    state = read_json(project.task_state)
    awaiting = bool(state.get("tasks")) and state.get("approved") is not True
    if awaiting and config.get("approval_gate") is not False:
        deny(
            f"Plan awaiting approval: {len(state['tasks'])} task(s) from {state.get('sdd_path') or 'the SDD'} "
            "have not been approved, so source edits are blocked. Show the user the task list and ask "
            "them to reply 'approved'. (To work outside this plan, the user can run "
            "/takshak:checkpoint clear-plan, or set approval_gate: false in .claude/framework.json.)"
        )
        return
    if reason := work_item_block(project):
        deny(reason)


def raw_is_patch(path: Path, project: Project, tool_name: str, tool_input: dict[str, Any]) -> bool:
    """A `git apply`/`patch` Bash command has no knowable target: treat it as a source write."""
    return tool_name == "Bash" and path == project.root.resolve()


if __name__ == "__main__":
    run_safely(main)
