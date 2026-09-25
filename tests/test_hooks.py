"""Every hook is driven the way Claude Code drives it: a JSON payload on stdin.

The v1 hooks read CLAUDE_TOOL_INPUT / CLAUDE_SESSION_ID env vars that Claude Code never
sets, and printed to stdout from Stop/PostToolUse where Claude never sees it. These tests
pin the real contract so that cannot regress silently again.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from conftest import Proj


def test_hooks_are_silent_outside_takshak_projects(tmp_path: Path) -> None:
    bare = Proj(tmp_path / "bare", tmp_path / "data")
    bare.root.mkdir()
    for name in ("session_start", "prompt_submit", "approval_gate", "quality_gate", "stop_checklist"):
        result = bare.hook(name, {"prompt": "approved", "tool_name": "Edit", "tool_input": {"file_path": "x.py"}})
        assert result.stdout == "", name
    assert not (bare.root / ".claude").exists()


# ---------------------------------------------------------------- SessionStart

def test_session_start_injects_memories_every_session(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan())
    memdir = project.data / "proj" / "memory"  # parent of transcript_path, as Claude Code lays it out
    memdir.mkdir(parents=True)
    (memdir / "design_limiter.md").write_text(
        "---\nname: limiter-store\ndescription: token bucket state lives in redis\ntype: project\n---\nUse redis INCR.\n"
    )
    first = project.hook("session_start").stdout
    second = project.hook("session_start", {"session_id": "s2"}).stdout
    assert "[MEMORY] limiter-store" in first
    assert "[MEMORY] limiter-store" in second  # v1 fired once per repo lifetime (session id was always "unknown")


def test_session_start_checkpoint_with_naive_timestamp(project: Proj) -> None:
    from datetime import datetime

    project.write(".claude/checkpoint.json", {
        "timestamp": datetime.utcnow().replace(microsecond=0).isoformat(),  # no offset: used to raise TypeError
        "active_skill": "sdd-implementer", "active_task_id": "2", "phase": 2,
        "next_step": "wire decr into middleware.py:41",
    })
    out = project.hook("session_start").stdout
    assert "[CHECKPOINT]" in out and "middleware.py:41" in out


def test_session_start_reports_pending_approval_and_recheck(project: Proj, plan) -> None:
    state = plan()
    state["tasks"][1]["needs_recheck"] = True
    project.write(".claude/task_state.json", state)
    out = project.hook("session_start").stdout
    assert "Awaiting approval" in out and "needs_recheck" in out


def test_session_start_migrates_config_and_syncs_statusline(project: Proj) -> None:
    project.write(".claude/framework.json", {"project_name": "app"})
    out = project.hook("session_start").stdout
    config = project.read(".claude/framework.json")
    assert config["project_name"] == "app"
    assert config["approval_gate"] is True and config["jira_transitions"]["prod"] == "Done"
    assert "gained new settings" in out
    for name in ("budget_sentinel.py", "_common.py", "run.sh"):
        assert (project.data / "statusline" / name).is_file()
    assert "takshak_hook_state.json" in (project.claude / ".gitignore").read_text()


def test_session_start_flags_legacy_wiring(project: Proj) -> None:
    project.write(".claude/settings.json", {"hooks": {"Stop": [{"hooks": [{"command": "python .claude/hooks/stop_checklist.py"}]}]}})
    assert "Legacy copy-installed hooks" in project.hook("session_start").stdout


# ---------------------------------------------------------------- approval

def test_user_approval_flips_flag(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan())
    out = project.hook("prompt_submit", {"prompt": "Approved, go ahead"}).stdout
    state = project.read(".claude/task_state.json")
    assert state["approved"] is True and state["approved_by"] == "user-prompt"
    assert "[APPROVAL]" in out


def test_approval_records_latency_metric(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan(tasks_created_at="2020-01-01T00:00:00+00:00"))
    project.hook("prompt_submit", {"prompt": "approved"})
    rows = metrics_rows(project)
    assert len(rows) == 1
    row = rows[0]
    assert row["event"] == "approval" and row["task_count"] == 2
    assert row["latency_seconds"] > 0  # 2020 -> now is a large, positive latency


def test_approval_metric_latency_is_null_without_tasks_created_at(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan())  # no tasks_created_at
    project.hook("prompt_submit", {"prompt": "approved"})
    assert metrics_rows(project)[0]["latency_seconds"] is None


def test_non_approval_prompts_do_not_flip(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan())
    for prompt in ("not approved yet", "why is this approved?", "add approval step"):
        project.hook("prompt_submit", {"prompt": prompt})
    assert project.read(".claude/task_state.json")["approved"] is False


def gate(project: Proj, tool: str, tool_input: dict) -> dict:
    return project.hook_json("approval_gate", {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input})


def denied(result: dict) -> bool:
    return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def test_source_edits_blocked_until_approved(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan())
    assert denied(gate(project, "Write", {"file_path": str(project.root / "src/limiter.py"), "content": "x"}))
    assert denied(gate(project, "Edit", {"file_path": "src/limiter.py", "old_string": "a", "new_string": "b"}))
    assert not denied(gate(project, "Write", {"file_path": str(project.root / "docs/sdd/rate-limit.md"), "content": "x"}))
    assert not denied(gate(project, "Edit", {"file_path": str(project.root / "README.md"), "old_string": "a", "new_string": "b"}))
    assert not denied(gate(project, "Edit", {"file_path": str(project.claude / "checkpoint.json"), "old_string": "a", "new_string": "b"}))

    project.hook("prompt_submit", {"prompt": "approved"})
    assert not denied(gate(project, "Write", {"file_path": str(project.root / "src/limiter.py"), "content": "x"}))


def test_claude_cannot_self_approve(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan())
    ts = str(project.claude / "task_state.json")
    assert denied(gate(project, "Edit", {"file_path": ts, "old_string": '"approved": false', "new_string": '"approved": true'}))
    assert denied(gate(project, "Write", {"file_path": ts, "content": json.dumps(plan(approved=True))}))
    assert denied(gate(project, "MultiEdit", {"file_path": ts, "edits": [{"old_string": "false", "new_string": '"approved": true'}]}))
    # ordinary task bookkeeping is fine
    assert not denied(gate(project, "Edit", {"file_path": ts, "old_string": '"status": "pending"', "new_string": '"status": "in_progress"'}))


def test_approval_gate_can_be_disabled(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan())
    config = project.read(".claude/framework.json")
    config["approval_gate"] = False
    project.write(".claude/framework.json", config)
    assert not denied(gate(project, "Write", {"file_path": str(project.root / "src/a.py"), "content": "x"}))


def test_no_plan_means_no_gate(project: Proj) -> None:
    assert not denied(gate(project, "Write", {"file_path": str(project.root / "src/a.py"), "content": "x"}))


# ---------------------------------------------------------------- quality gate

FAKE_LINT = """
import sys
path = sys.argv[1]
if "dirty" in path:
    print(f"{path}:3:1: F401 [*] `os` imported but unused")
    print(f"{path}:9:5: E711 comparison to None")
    print("Found 2 errors.")
"""

FAKE_MYPY = """
import sys
path = sys.argv[1]
if "dirty" in path:
    print(f'{path}:12: error: Incompatible return value type (got "int", expected "str")  [return-value]')
"""


def configure_gate(project: Proj) -> None:
    lint = project.write("tools/fake_lint.py", FAKE_LINT)
    mypy = project.write("tools/fake_mypy.py", FAKE_MYPY)
    config = project.read(".claude/framework.json")
    config["lint_command"] = f'"{sys.executable}" "{lint}" {{file}}'
    config["typecheck_command"] = f'"{sys.executable}" "{mypy}" {{file}}'
    project.write(".claude/framework.json", config)


def test_quality_gate_reports_to_claude_via_additional_context(project: Proj) -> None:
    configure_gate(project)
    target = project.write("src/dirty.py", "import os\n")
    result = project.hook_json("quality_gate", {"hook_event_name": "PostToolUse", "tool_name": "Edit", "tool_input": {"file_path": str(target)}})
    ctx = result["hookSpecificOutput"]["additionalContext"]
    assert result["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "F401" in ctx and "return-value" in ctx and "lint 2, types 1" in ctx
    registry = project.read(".claude/anti_pattern_registry.json")
    assert registry["F401"]["count"] == 1 and registry["mypy:return-value"]["count"] == 1


def test_quality_gate_pattern_alert_on_recurrence(project: Proj) -> None:
    configure_gate(project)
    target = project.write("src/dirty.py", "import os\n")
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(target)}}
    project.hook("quality_gate", payload)
    ctx = project.hook_json("quality_gate", payload)["hookSpecificOutput"]["additionalContext"]
    assert "[PATTERN] recurring" in ctx and "F401 x2" in ctx


def test_quality_gate_silent_when_clean_or_out_of_scope(project: Proj) -> None:
    configure_gate(project)
    clean = project.write("src/clean.py", "x = 1\n")
    assert project.hook("quality_gate", {"tool_input": {"file_path": str(clean)}}).stdout == ""
    assert project.hook("quality_gate", {"tool_input": {"file_path": str(project.root / "notes.md")}}).stdout == ""
    assert project.hook("quality_gate", {"tool_input": {"file_path": str(project.root / ".venv/dirty.py")}}).stdout == ""


def metrics_rows(project: Proj) -> list[dict]:
    path = project.claude / "metrics.jsonl"
    if not path.is_file():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_quality_gate_records_a_metric_even_when_clean(project: Proj) -> None:
    """A silent stdout (clean file) must still leave a metrics row -- the hit-rate
    denominator needs every gate run, not just the ones with findings."""
    configure_gate(project)
    clean = project.write("src/clean.py", "x = 1\n")
    project.hook("quality_gate", {"tool_input": {"file_path": str(clean)}})
    dirty = project.write("src/dirty.py", "import os\n")
    project.hook("quality_gate", {"tool_input": {"file_path": str(dirty)}})
    rows = metrics_rows(project)
    assert len(rows) == 2
    assert rows[0]["event"] == "quality_gate" and rows[0]["clean"] is True and rows[0]["lint"] == 0
    assert rows[1]["clean"] is False and rows[1]["lint"] == 2 and rows[1]["types"] == 1

    # an out-of-scope file (no gate_extensions match, or skipped dir) never even runs the
    # gate, so it must not add a metrics row either
    project.hook("quality_gate", {"tool_input": {"file_path": str(project.root / "notes.md")}})
    assert len(metrics_rows(project)) == 2


def test_quality_gate_reports_missing_tool(project: Proj) -> None:
    config = project.read(".claude/framework.json")
    config["lint_command"] = "definitely-not-a-linter {file}"
    project.write(".claude/framework.json", config)
    target = project.write("src/a.py", "x = 1\n")
    ctx = project.hook_json("quality_gate", {"tool_input": {"file_path": str(target)}})["hookSpecificOutput"]["additionalContext"]
    assert "could not run" in ctx


def test_quality_gate_flags_broken_toolchain_once_per_session(project: Proj) -> None:
    config = project.read(".claude/framework.json")
    config["lint_command"] = f'"{sys.executable}" -m definitely_not_installed_linter {{file}}'
    project.write(".claude/framework.json", config)
    target = project.write("src/a.py", "x = 1\n")
    payload = {"tool_input": {"file_path": str(target)}}
    ctx = project.hook_json("quality_gate", payload)["hookSpecificOutput"]["additionalContext"]
    assert "lint command failed without findings" in ctx and "No module named" in ctx
    assert project.hook("quality_gate", payload).stdout == ""
    assert "failed without findings" in project.hook_json("quality_gate", {**payload, "session_id": "s2"})["hookSpecificOutput"]["additionalContext"]


# ---------------------------------------------------------------- Stop

def test_stop_blocks_for_staged_amendments_once(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan(approved=True))
    project.write(".claude/amendments_pending.json", {"candidates": [{"gap": "bucket keyed by user not tenant"}], "style_candidates": []})
    first = project.hook_json("stop_checklist")
    assert first["decision"] == "block" and "amendment" in first["reason"]
    second = project.hook_json("stop_checklist")
    assert "decision" not in second  # same checklist: no loop
    assert "still pending" in second.get("systemMessage", "")


def test_stop_never_blocks_when_stop_hook_active(project: Proj) -> None:
    project.write(".claude/amendments_pending.json", {"candidates": [{"gap": "x"}], "style_candidates": []})
    assert "decision" not in project.hook_json("stop_checklist", {"stop_hook_active": True})


def test_stop_delivers_budget_breach_and_acknowledges(project: Proj) -> None:
    project.write(".claude/budget.json", {"alert": {"tripped": True, "window": "five_hour", "used_percentage": 96, "threshold": 95}})
    result = project.hook_json("stop_checklist")
    assert result["decision"] == "block" and "BUDGET 96%" in result["reason"]
    assert project.read(".claude/budget.json")["alert"]["acknowledged"] is True
    # and the prompt-submit fallback stays quiet once delivered
    assert "[BUDGET]" not in project.hook("prompt_submit", {"prompt": "hi"}).stdout


def test_prompt_submit_relays_undelivered_budget_breach(project: Proj) -> None:
    project.write(".claude/budget.json", {"alert": {"tripped": True, "window": "seven_day", "used_percentage": 97, "threshold": 95}})
    assert "[BUDGET] 97%" in project.hook("prompt_submit", {"prompt": "continue"}).stdout
    assert project.hook("prompt_submit", {"prompt": "continue"}).stdout == ""


def test_stop_info_is_user_facing_and_deduplicated(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan(approved=True, last_memory_write="2020-01-01T00:00:00"))
    first = project.hook_json("stop_checklist")
    assert "decision" not in first
    assert "2 task(s) open" in first["systemMessage"]
    assert "systemMessage" not in project.hook_json("stop_checklist")


def test_stop_flags_empty_checkpoint_during_implementation(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan(approved=True, statuses=("completed", "in_progress")))
    result = project.hook_json("stop_checklist")
    assert result["decision"] == "block" and "checkpoint.json is empty" in result["reason"]


def test_stop_offers_retro_once(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan(approved=True, statuses=("completed", "completed")))
    assert "/takshak:retro" in project.hook_json("stop_checklist")["systemMessage"]
    assert project.read(".claude/task_state.json")["retro_offered"] is True


def test_drift_ignores_bare_digits_and_matches_real_refs(project: Proj, plan) -> None:
    project.init_git()
    project.write(".claude/task_state.json", plan(approved=True, last_memory_write="2099-01-01T00:00:00+00:00"))
    for msg in ("bump version to 1.2", "fix 2 flaky tests", "update docs for release 1"):
        project.commit(msg)
    assert "map to no planned task" in project.hook_json("stop_checklist")["systemMessage"]

    other = Proj(project.root.parent / "other", project.data)
    other.root.mkdir()
    other.claude.mkdir()
    (other.claude / "framework.json").write_text((project.claude / "framework.json").read_text())
    other.init_git()
    other.write(".claude/task_state.json", plan(approved=True, last_memory_write="2099-01-01T00:00:00+00:00"))
    for msg in ("T1: token bucket schema", "PAY-78 limiter wiring", "middleware tweak for limiter"):
        other.commit(msg)
    assert "map to no planned task" not in other.hook_json("stop_checklist").get("systemMessage", "")


# ---------------------------------------------------------------- statusline

def test_statusline_trips_alert_and_renders(project: Proj) -> None:
    from conftest import HOOKS, run_script

    payload = {
        "model": {"display_name": "Opus"},
        "workspace": {"project_dir": str(project.root)},
        "context_window": {"used_percentage": 41, "context_window_size": 1000000},
        "rate_limits": {"five_hour": {"used_percentage": 96.5, "resets_at": 4102444800}},
    }
    env = project.env
    env.pop("CLAUDE_PROJECT_DIR")  # the statusline gets the project from its payload
    result = run_script(HOOKS / "budget_sentinel.py", payload, env)
    assert "CHECKPOINT NOW" in result.stdout and "5h 96% !" in result.stdout
    budget = project.read(".claude/budget.json")
    assert budget["alert"]["tripped"] is True and budget["alert"]["acknowledged"] is False
    assert (project.claude / "budget_history.jsonl").is_file()


def test_statusline_does_not_write_state_in_other_projects(tmp_path: Path) -> None:
    from conftest import HOOKS, run_script

    other = tmp_path / "other"
    other.mkdir()
    result = run_script(HOOKS / "budget_sentinel.py", {"model": {"display_name": "Opus"}, "cwd": str(other)},
                        {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")})
    assert "Opus" in result.stdout
    assert not (other / ".claude").exists()
