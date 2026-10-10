"""PostToolUse hook (Edit|Write|MultiEdit) -- remember which source files changed since the
docs were last reconciled.

Deliberately silent: it only appends to .claude/reconcile_state.json. Nagging every edit
would be wallpaper; the Stop hook decides when enough has changed ("a task finished", or
`reconcile_edit_threshold` files moved) to ask for /takshak:reconcile.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import Project, project_root, read_payload, run_safely  # noqa: E402
from _reconcile import enabled, record_edit, relative  # noqa: E402


def main() -> None:
    payload = read_payload()
    project = Project(project_root(payload))
    if not project.enabled or not enabled(project):
        return
    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("path")
    if not raw:
        return
    rel = relative(project, str(raw))
    if rel:
        record_edit(project, rel)


if __name__ == "__main__":
    run_safely(main)
