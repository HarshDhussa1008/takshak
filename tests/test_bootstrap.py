from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import ROOT


def bootstrap(project: Path, *args: str, data: Path | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(ROOT / "tools" / "bootstrap.py"), args[0], "--project", str(project), *args[1:]]
    if data:
        cmd += ["--plugin-data", str(data)]
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    return subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)


def test_init_detects_python_stack_and_is_idempotent(tmp_path: Path) -> None:
    proj = tmp_path / "payments"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='p'\n")
    first = bootstrap(proj, "init", data=tmp_path / "data")
    assert first.returncode == 0, first.stderr
    config = json.loads((proj / ".claude" / "framework.json").read_text())
    assert config["project_name"] == "payments" and config["languages"] == ["python"]
    assert "ruff" in config["lint_command"] and "{file}" in config["lint_command"]
    for seed in ("task_state.json", "checkpoint.json", "amendments_pending.json", "anti_pattern_registry.json"):
        assert (proj / ".claude" / seed).is_file()
    assert (proj / "CLAUDE.md").is_file()
    local = json.loads((proj / ".claude" / "settings.local.json").read_text())
    assert "budget_sentinel.py" in local["statusLine"]["command"] and local["statusLine"]["refreshInterval"] == 30
    assert (tmp_path / "data" / "statusline" / "budget_sentinel.py").is_file()

    config["build_command"] = "make deploy"
    (proj / ".claude" / "framework.json").write_text(json.dumps(config))
    again = bootstrap(proj, "init", data=tmp_path / "data")
    assert "[SKIP]  framework.json exists" in again.stdout
    assert json.loads((proj / ".claude" / "framework.json").read_text())["build_command"] == "make deploy"


def test_init_detects_typescript(tmp_path: Path) -> None:
    proj = tmp_path / "web"
    proj.mkdir()
    (proj / "package.json").write_text(json.dumps({"devDependencies": {"eslint": "^9", "typescript": "^5"}}))
    (proj / "tsconfig.json").write_text("{}")
    bootstrap(proj, "init", "--no-statusline")
    config = json.loads((proj / ".claude" / "framework.json").read_text())
    assert config["languages"] == ["typescript"] and ".tsx" in config["gate_extensions"]
    assert "eslint" in config["lint_command"] and "tsc" in config["project_typecheck_command"]


def test_team_settings_enable_plugin_with_auto_update(tmp_path: Path) -> None:
    proj = tmp_path / "svc"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}}))
    bootstrap(proj, "team", "--marketplace-repo", "tally/takshak")
    settings = json.loads((proj / ".claude" / "settings.json").read_text())
    assert settings["permissions"]["allow"] == ["Bash(ls)"]
    market = settings["extraKnownMarketplaces"]["takshak"]
    assert market["source"] == {"source": "github", "repo": "tally/takshak"} and market["autoUpdate"] is True
    assert settings["enabledPlugins"]["takshak@takshak"] is True


def test_migrate_removes_legacy_wiring_but_keeps_user_hooks(tmp_path: Path) -> None:
    proj = tmp_path / "old"
    (proj / ".claude" / "hooks").mkdir(parents=True)
    (proj / ".claude" / "hooks" / "stop_checklist.py").write_text("# legacy")
    (proj / ".claude" / "hooks" / "my_own_hook.py").write_text("# user")
    (proj / ".claude" / "settings.json").write_text(json.dumps({
        "statusLine": {"type": "command", "command": "python .claude/hooks/budget_sentinel.py"},
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "python .claude/hooks/stop_checklist.py"}]}],
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "./guard.sh"}]}],
        },
    }))
    warn = bootstrap(proj, "init", "--no-statusline")
    assert "legacy copy-installed hooks detected" in warn.stdout
    bootstrap(proj, "init", "--no-statusline", "--migrate")
    settings = json.loads((proj / ".claude" / "settings.json").read_text())
    assert "statusLine" not in settings and "Stop" not in settings["hooks"]
    assert settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "./guard.sh"
    assert not (proj / ".claude" / "hooks" / "stop_checklist.py").exists()
    assert (proj / ".claude" / "hooks" / "my_own_hook.py").exists()


def test_doctor_reports_placeholder_build(tmp_path: Path) -> None:
    proj = tmp_path / "d"
    proj.mkdir()
    bootstrap(proj, "init", "--no-statusline")
    result = bootstrap(proj, "doctor")
    assert result.returncode == 1
    assert "build_command is still the placeholder" in result.stdout
    assert "statusline not wired" in result.stdout
