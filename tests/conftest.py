from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
TEMPLATES = ROOT / "templates"


def run_script(script: Path, payload: dict[str, Any] | None, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        input=json.dumps(payload) if payload is not None else "",
        capture_output=True, text=True, env=env, timeout=60, check=False,
    )


class Proj:
    def __init__(self, root: Path, data: Path) -> None:
        self.root = root
        self.data = data
        self.claude = root / ".claude"

    @property
    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(self.root)
        env["CLAUDE_PLUGIN_DATA"] = str(self.data)
        env["TAKSHAK_DEBUG"] = "1"  # surface exceptions instead of swallowing them
        return env

    def write(self, rel: str, obj: Any) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(obj if isinstance(obj, str) else json.dumps(obj, indent=2), encoding="utf-8")
        return path

    def read(self, rel: str) -> Any:
        return json.loads((self.root / rel).read_text(encoding="utf-8"))

    def hook(self, name: str, payload: dict[str, Any] | None = None) -> subprocess.CompletedProcess[str]:
        base = {"session_id": "s1", "cwd": str(self.root), "transcript_path": str(self.data / "proj" / "s1.jsonl")}
        base.update(payload or {})
        result = run_script(HOOKS / f"{name}.py", base, self.env)
        assert result.returncode == 0, result.stderr
        return result

    def hook_json(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        out = self.hook(name, payload).stdout.strip()
        return json.loads(out) if out else {}

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, check=True).stdout

    def init_git(self) -> None:
        self.git("init", "-q")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")

    def commit(self, message: str) -> None:
        marker = self.root / "log.txt"
        marker.write_text(marker.read_text() + message + "\n" if marker.exists() else message + "\n")
        self.git("add", "log.txt")
        self.git("commit", "-qm", message)


@pytest.fixture
def project(tmp_path: Path) -> Proj:
    root = tmp_path / "app"
    (root / ".claude").mkdir(parents=True)
    config = json.loads((TEMPLATES / "framework.json").read_text())
    config.update({"project_name": "app", "lint_command": None, "typecheck_command": None})
    (root / ".claude" / "framework.json").write_text(json.dumps(config))
    for seed in (TEMPLATES / "state").glob("*.json"):
        (root / ".claude" / seed.name).write_text(seed.read_text())
    return Proj(root, tmp_path / "plugin-data")


@pytest.fixture
def plan() -> Callable[..., dict[str, Any]]:
    def make(approved: bool = False, statuses: tuple[str, ...] = ("pending", "pending"), **extra: Any) -> dict[str, Any]:
        state = json.loads((TEMPLATES / "state" / "task_state.json").read_text())
        state.update({
            "sdd_path": "docs/sdd/rate-limit.md",
            "approved": approved,
            "tasks": [
                {"id": str(i + 1), "title": title, "status": status, "needs_recheck": False, "jira_key": f"PAY-{77 + i}"}
                for i, (title, status) in enumerate(zip(["Add token bucket schema", "Wire limiter middleware"], statuses, strict=False))
            ],
        })
        state.update(extra)
        return state
    return make
