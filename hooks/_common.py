"""Shared plumbing for every takshak hook.

The Claude Code hook contract, in one place so no hook gets it wrong again:
- Input arrives as JSON on stdin. There are no CLAUDE_TOOL_INPUT / CLAUDE_SESSION_ID env vars.
- Plain stdout reaches Claude only for SessionStart and UserPromptSubmit.
- PostToolUse speaks through hookSpecificOutput.additionalContext.
- Stop speaks through {"decision": "block", "reason": ...} (Claude keeps going and reads
  the reason) or a top-level systemMessage (shown to the user).
- PreToolUse denies through hookSpecificOutput.permissionDecision.

Every hook is a no-op in a project that has no .claude/framework.json, so enabling the
plugin user-wide never spams unrelated repos.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if _reconfigure := getattr(sys.stdout, "reconfigure", None):
    try:
        _reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        pass

CONFIG_NAME = "framework.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": 2,
    "project_name": "my-project",
    "jira_project_key": None,
    "jira_integration": False,
    "jira_subtask_type": "Subtask",
    "jira_transitions": {"staging": "In Review", "prod": "Done"},
    "slack_mcp_channel": None,
    "slack_integration": False,
    "build_command": "echo '[ship] Configure build_command in .claude/framework.json'",
    "test_command": "python -m pytest . -v --tb=short",
    "lint_command": "python -m ruff check --fix {file}",
    "typecheck_command": "python -m mypy {file} --ignore-missing-imports --no-error-summary",
    "project_typecheck_command": "python -m mypy . --ignore-missing-imports",
    "gate_extensions": [".py"],
    "approval_gate": True,
    "budget_alert_threshold": 95,
    "sdd_path": "docs/sdd",
    "memory_path": None,
    "languages": ["python"],
}


def read_payload() -> dict[str, Any]:
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def project_root(payload: dict[str, Any] | None = None) -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    payload = payload or {}
    workspace = payload.get("workspace") or {}
    for candidate in (workspace.get("project_dir"), payload.get("cwd"), workspace.get("current_dir")):
        if candidate:
            return Path(candidate)
    return Path.cwd()


class Project:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.state_dir = root / ".claude"
        self.config_path = self.state_dir / CONFIG_NAME
        self.task_state = self.state_dir / "task_state.json"
        self.checkpoint = self.state_dir / "checkpoint.json"
        self.budget = self.state_dir / "budget.json"
        self.budget_history = self.state_dir / "budget_history.jsonl"
        self.registry = self.state_dir / "anti_pattern_registry.json"
        self.amendments = self.state_dir / "amendments_pending.json"
        self.hook_state = self.state_dir / "takshak_hook_state.json"
        self.metrics = self.state_dir / "metrics.jsonl"

    @property
    def enabled(self) -> bool:
        return self.config_path.is_file()

    def config(self) -> dict[str, Any]:
        merged = dict(DEFAULT_CONFIG)
        merged.update(read_json(self.config_path))
        return merged


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def parse_ts(value: object) -> datetime | None:
    """ISO-8601 to an aware UTC datetime. Naive timestamps are treated as UTC; subtracting
    naive from aware raises TypeError, which used to crash hooks mid-session."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def age_seconds(value: object) -> float | None:
    parsed = parse_ts(value)
    if parsed is None:
        return None
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git(root: Path, *args: str, timeout: float = 5) -> str:
    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True, cwd=root,
            timeout=timeout, check=False, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def claude_slug(path: Path) -> str:
    """Claude Code names project folders by replacing every non-alphanumeric character
    with '-', with no collapsing or stripping: /home/u/app -> -home-u-app, D:\\app -> D--app."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def memory_dir(project: Project, payload: dict[str, Any]) -> Path:
    override = project.config().get("memory_path")
    if override:
        return Path(os.path.expanduser(str(override)))
    transcript = payload.get("transcript_path")
    if transcript:
        # The transcript lives in the exact project folder Claude Code uses, so its parent
        # is authoritative -- no need to re-derive the slug and risk getting it wrong.
        return Path(transcript).parent / "memory"
    return Path.home() / ".claude" / "projects" / claude_slug(project.root) / "memory"


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload))


def emit_context(event: str, text: str) -> None:
    emit({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})


def hook_state(project: Project) -> dict[str, Any]:
    return read_json(project.hook_state)


def save_hook_state(project: Project, state: dict[str, Any]) -> None:
    write_json(project.hook_state, state)


METRICS_MAX_LINES = 5000


def record_metric(project: Project, event: str, **fields: Any) -> None:
    """Append one event to .claude/metrics.jsonl (append-only, capped like budget_history).
    Never raises: metrics are an observability nice-to-have, not load-bearing."""
    row = {"t": now_iso(), "event": event, **fields}
    try:
        project.state_dir.mkdir(parents=True, exist_ok=True)
        with project.metrics.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        lines = project.metrics.read_text(encoding="utf-8").splitlines()
        if len(lines) > METRICS_MAX_LINES:
            tmp = project.metrics.with_suffix(".jsonl.tmp")
            tmp.write_text("\n".join(lines[-METRICS_MAX_LINES:]) + "\n", encoding="utf-8")
            os.replace(tmp, project.metrics)
    except OSError:
        pass


def read_metrics(project: Project) -> list[dict[str, Any]]:
    try:
        lines = project.metrics.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for ln in lines:
        if not ln.strip():
            continue
        try:
            parsed = json.loads(ln)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def run_safely(main: Any) -> None:
    """Hooks must never break the session: swallow everything, always exit 0."""
    try:
        main()
    except Exception as exc:  # noqa: BLE001 -- last-resort guard for a hook process
        if os.environ.get("TAKSHAK_DEBUG"):
            raise
        print(f"[takshak] hook error suppressed: {type(exc).__name__}: {exc}", file=sys.stderr)
    sys.exit(0)
