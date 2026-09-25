"""Static checks on the plugin layout: manifests, hook wiring, skill/agent frontmatter,
and that no skill points at a command or agent that does not exist."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import ROOT


def clean_env(**extra: str) -> dict[str, str]:
    import os

    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    env.update(extra)
    return env


def _resolve_git_bash() -> str | None:
    """Best-effort discovery of a real, native-path-capable bash -- mirrors how Claude
    Code itself resolves the interpreter for a hook command (see CLAUDE_CODE_GIT_BASH_PATH
    in Claude Code's settings docs). A bare `bash` found via PATH is not trustworthy
    evidence on Windows: when WSL is installed, `C:\\WINDOWS\\system32\\bash.exe` (WSL's
    launcher stub) commonly sits ahead of Git Bash on PATH. That stub runs inside WSL's
    own Linux filesystem view (paths like /mnt/d/...), and cannot open a native Windows
    path in any spelling -- forward-slash, backslash or MSYS /d/... alike -- so it is a
    different, incompatible interpreter, not an alternate way to reach Git Bash."""
    import os

    override = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    if override and Path(override).is_file():
        return override
    for candidate in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("bash")
    if found and "System32" not in found and "WindowsApps" not in found:
        return found
    return None


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} has no YAML frontmatter"
    block = text.split("---", 2)[1]
    return {k.strip(): v.strip() for k, v in (ln.split(":", 1) for ln in block.splitlines() if ":" in ln)}


SKILLS = sorted(p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md"))
AGENTS = sorted(p.stem for p in (ROOT / "agents").glob("*.md"))


def test_manifests() -> None:
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert plugin["name"] == "takshak"
    assert market["plugins"][0]["name"] == plugin["name"]
    assert market["plugins"][0]["source"] == "./"


def test_hooks_json_references_real_scripts_with_second_timeouts() -> None:
    config = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    events = set(config["hooks"])
    assert {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"} <= events
    for groups in config["hooks"].values():
        for group in groups:
            for hook in group["hooks"]:
                scripts = re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([\w/.]+)", hook["command"])
                assert scripts and all((ROOT / s).is_file() for s in scripts), hook["command"]
                assert 1 <= hook["timeout"] <= 120, "timeouts are seconds, not milliseconds"


def test_run_sh_invoked_via_bash_with_forward_slashes() -> None:
    """On Windows, Git Bash's MSYS runtime reparses the inherited command line itself and
    treats an unescaped backslash as an escape character -- a raw native Windows path
    (D:\\takshak\\hooks\\run.sh) gets silently mangled (D:takshakhooksrun.sh) and
    bash fails to find the file. The fix is forward slashes in every command string, never
    a literal backslash path. `bash` (not `sh`, which is not guaranteed to be on PATH) is
    the interpreter every hook and skill uses to invoke run.sh."""
    hooks_config = (ROOT / "hooks" / "hooks.json").read_text()
    assert "\\\\" not in hooks_config, "hooks.json command strings must use forward slashes only"
    assert hooks_config.count('"bash \\"${CLAUDE_PLUGIN_ROOT}/hooks/run.sh\\"') == 5

    for name in ("dashboard", "doctor", "init"):
        text = (ROOT / "skills" / name / "SKILL.md").read_text()
        assert 'bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh"' in text
        assert "\\hooks\\run.sh" not in text, f"{name}: literal backslash path in run.sh invocation"


def test_every_skill_has_frontmatter_matching_its_directory() -> None:
    for name in SKILLS:
        meta = frontmatter(ROOT / "skills" / name / "SKILL.md")
        assert meta.get("name") == name
        assert len(meta.get("description", "")) > 40, f"{name}: description too thin to trigger on"


def test_every_agent_has_frontmatter() -> None:
    for name in AGENTS:
        meta = frontmatter(ROOT / "agents" / f"{name}.md")
        assert meta.get("name") == name and meta.get("description") and meta.get("tools")
        assert "Write" not in meta["tools"] and "Edit" not in meta["tools"], "reviewers are read-only"


def test_skills_reference_only_real_commands_and_agents() -> None:
    corpus = [p for p in ROOT.glob("skills/**/*.md")] + [ROOT / "templates" / "CLAUDE.md.template", ROOT / "README.md"]
    for path in corpus:
        text = path.read_text(encoding="utf-8")
        for ref in re.findall(r"/takshak:([a-z-]+)", text):
            assert ref in SKILLS, f"{path.name} references missing /takshak:{ref}"
        for ref in re.findall(r"`takshak:([a-z-]+)`", text):
            assert ref in AGENTS or ref in SKILLS, f"{path.name} references missing agent takshak:{ref}"
        assert "run `/compact`" not in text and "run /compact" not in text, f"{path.name}: Claude cannot run /compact"
        for phantom in ("/remember ", "/review ", "/resume ", "/checkpoint clear"):
            for line in text.splitlines():
                if phantom in line and "/takshak:" not in line:
                    raise AssertionError(f"{path.name}: un-namespaced {phantom.strip()} -> {line.strip()}")


def test_deploy_skill_is_never_auto_invoked() -> None:
    assert frontmatter(ROOT / "skills" / "ship" / "SKILL.md").get("disable-model-invocation") == "true"


@pytest.mark.parametrize("interpreter", ["sh", "bash"])
def test_run_sh_passes_stdin_through(tmp_path: Path, interpreter: str) -> None:
    """Must round-trip cleanly under both interpreters -- run.sh has no bashisms, so
    anyone sourcing or running it directly with either `sh` or `bash` gets the same
    behavior. `bash` is what hooks.json and the skills actually invoke; `sh` is skipped
    here if it isn't on PATH (not guaranteed on Windows) rather than treated as a failure.

    Every path is passed as forward-slash-only (`.as_posix()`), matching how hooks.json
    invokes run.sh -- never a raw native backslash path as a discrete argv element, which
    on Windows/Git Bash gets reparsed by the MSYS runtime and mangled (D:\\takshak\\...
    -> D:takshak...). That reparsing is specific to backslashes; forward slashes pass
    through a plain argv list unchanged, so no extra shell wrapping is needed here.

    The interpreter itself is resolved via `_resolve_git_bash()` rather than trusting a
    bare "bash"/"sh" name, since PATH can hand back WSL's bash instead of Git Bash's (see
    that helper's docstring) -- a case this test skips rather than misreports as a bug."""
    found = shutil.which(interpreter) if interpreter == "sh" else _resolve_git_bash()
    if found is None:
        pytest.skip(f"no usable {interpreter} found")
        return
    exe = found

    echo = tmp_path / "echo.py"
    echo.write_text("import sys; print(sys.stdin.read().upper())")
    data_dir = tmp_path / f"d-{interpreter}"
    run_sh = (ROOT / "hooks" / "run.sh").as_posix()

    def invoke(script: Path, payload: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([exe, run_sh, script.as_posix()], input=payload, capture_output=True,
                              text=True, env=clean_env(CLAUDE_PLUGIN_DATA=str(data_dir)), check=False)

    result = invoke(echo, "payload")
    assert result.stdout.strip() == "PAYLOAD", result.stderr
    assert (data_dir / "python-path").is_file()
    cached = invoke(echo, "again")
    assert cached.stdout.strip() == "AGAIN", cached.stderr


def test_line_endings_pinned_for_shell_scripts() -> None:
    attrs = (ROOT / ".gitattributes").read_text()
    assert "*.sh text eol=lf" in attrs
