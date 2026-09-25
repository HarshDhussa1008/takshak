"""PostToolUse hook (Edit|Write|MultiEdit) -- lint + type check the file just edited.

Findings go back to Claude through hookSpecificOutput.additionalContext (plain stdout from
PostToolUse only reaches the debug log). Silent when the file is clean.

Codes are counted in anti_pattern_registry.json; a code that recurs is surfaced as a
pattern alert so it can become a feedback memory.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    Project,
    emit_context,
    hook_state,
    project_root,
    read_json,
    read_payload,
    record_metric,
    run_safely,
    save_hook_state,
    write_json,
)

ALERT_THRESHOLD = 2
MAX_DETAIL_LINES = 12
COMMAND_TIMEOUT = 40
ALWAYS_SKIP = {"__pycache__", "dist", "build", "node_modules", ".git", ".venv", "venv", ".tox", ".mypy_cache"}
LINT_CODE = re.compile(r"\b([A-Z]{1,4}\d{3,4})\b")
MYPY_CODE = re.compile(r"\[([a-z][a-z0-9-]+)\]\s*$")


def edited_file(project: Project, tool_input: dict[str, Any]) -> Path | None:
    raw = tool_input.get("file_path") or tool_input.get("path")
    if not raw:
        return None
    path = Path(str(raw))
    return path if path.is_absolute() else project.root / path


def should_skip(path: Path, extensions: list[str], project_name: str) -> bool:
    if extensions and path.suffix not in extensions:
        return True
    skip = set(ALWAYS_SKIP) | {f".{project_name}", f".venv-{project_name}"}
    return any(part in skip for part in path.parts)


def run_template(template: str, target: Path, cwd: Path) -> tuple[int, str]:
    """{file} is substituted per token after tokenizing: shlex treats Windows backslashes
    as escapes, so substituting into the string first mangles the path."""
    cmd = [tok.replace("{file}", str(target)) for tok in shlex.split(template)]
    if not cmd:
        return 0, ""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=cwd, timeout=COMMAND_TIMEOUT,
            check=False, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, f"could not run `{cmd[0]}`: {exc}"
    return result.returncode, (result.stdout + result.stderr).strip()


def finding_lines(output: str) -> list[str]:
    return [
        ln for ln in output.splitlines()
        if ln.strip() and (": error" in ln or ": warning" in ln or LINT_CODE.search(ln))
        and not ln.startswith(("Found ", "[*]", "Success"))
    ]


def broken_tool_note(project: Project, session: str, label: str, rc: int, out: str, found: list[str]) -> str | None:
    """A command that exits non-zero without a single parseable finding is a broken
    toolchain (missing module, bad path), not a clean file. Say so once per session
    instead of silently passing every edit."""
    if found or rc in (0, -1):
        return None
    memo = hook_state(project)
    key = f"broken:{label}"
    if memo.get(key) == session:
        return None
    memo[key] = session
    save_hook_state(project, memo)
    first = next((ln.strip() for ln in out.splitlines() if ln.strip()), f"exit code {rc}")
    return f"{label} command failed without findings ({first[:160]}). Fix it in .claude/framework.json or run /takshak:doctor."


def main() -> None:
    payload = read_payload()
    project = Project(project_root(payload))
    if not project.enabled:
        return
    config = project.config()
    target = edited_file(project, payload.get("tool_input") or {})
    if target is None or should_skip(target, list(config.get("gate_extensions") or []), str(config.get("project_name", ""))):
        return

    session = str(payload.get("session_id", ""))
    codes: set[str] = set()
    details: list[str] = []
    notes: list[str] = []
    summary: list[str] = []
    lint_count = 0
    type_count = 0

    if lint := config.get("lint_command"):
        rc, out = run_template(str(lint), target, project.root)
        if rc == -1:
            notes.append(out)
        lines = finding_lines(out)
        lint_count = len(lines)
        if note := broken_tool_note(project, session, "lint", rc, out, lines):
            notes.append(note)
        codes.update(m.group(1) for ln in lines if (m := LINT_CODE.search(ln)))
        details.extend(lines)
        summary.append(f"lint {len(lines)}")
        if re.search(r"\d+ fixed", out):
            notes.append("The linter auto-fixed this file; re-read it before the next edit.")

    if typecheck := config.get("typecheck_command"):
        rc, out = run_template(str(typecheck), target, project.root)
        if rc == -1:
            notes.append(out)
        lines = [ln for ln in out.splitlines() if ": error:" in ln or ": warning:" in ln]
        type_count = len(lines)
        if note := broken_tool_note(project, session, "typecheck", rc, out, lines):
            notes.append(note)
        codes.update(f"mypy:{m.group(1)}" for ln in lines if (m := MYPY_CODE.search(ln)))
        details.extend(lines)
        summary.append(f"types {len(lines)}")

    if lint or typecheck:
        record_metric(project, "quality_gate", file=target.name, lint=lint_count, types=type_count, clean=lint_count == 0 and type_count == 0)

    registry = read_json(project.registry)
    alerts = []
    for code in sorted(codes):
        entry = registry.get(code) or {"count": 0, "last_file": "", "memory_written": False}
        entry["count"] = int(entry.get("count", 0)) + 1
        entry["last_file"] = str(target)
        registry[code] = entry
        if entry["count"] >= ALERT_THRESHOLD and not entry.get("memory_written"):
            alerts.append(f"{code} x{entry['count']}")
    if codes:
        write_json(project.registry, registry)

    if not details and not notes:
        return

    text = [f"[GATE] {target.name}: " + ", ".join(summary)]
    text.extend(f"  {ln}" for ln in details[:MAX_DETAIL_LINES])
    if len(details) > MAX_DETAIL_LINES:
        text.append(f"  ... {len(details) - MAX_DETAIL_LINES} more")
    text.extend(f"  note: {n}" for n in notes)
    if alerts:
        text.append(
            f"[PATTERN] recurring: {', '.join(alerts)} -- fix the root habit; if it is a deliberate "
            "convention, record it with /takshak:remember."
        )
    emit_context("PostToolUse", "\n".join(text))


if __name__ == "__main__":
    run_safely(main)
