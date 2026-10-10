"""The pipeline as a state machine: next step, who moves, autopilot."""

from __future__ import annotations

import json
import subprocess
import sys

from conftest import ROOT, Proj
from test_design_stages import ADR, LLD, NOTES, SDD, SDD_REL

sys.path.insert(0, str(ROOT / "hooks"))
import _flow as flow  # noqa: E402
from _common import Project  # noqa: E402


def nxt(project: Proj) -> dict:
    return flow.next_step(Project(project.root))


def tool(name: str, project: Proj, *args: str) -> subprocess.CompletedProcess[str]:
    r = subprocess.run([sys.executable, str(ROOT / "tools" / f"{name}.py"), *args, "--project", str(project.root)],
                       capture_output=True, text=True, env=project.env, timeout=60, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
    return r


def stage_files(project: Proj, *, notes=True, adr=True, lld=True) -> None:
    project.write(SDD_REL, SDD)
    if notes:
        project.write("docs/design-notes/rate-limiter.md", NOTES)
    if adr:
        project.write("docs/adr/0001-redis-for-counters.md", ADR)
    if lld:
        project.write("docs/lld/rate-limiter.md", LLD)


def test_nothing_in_flight_is_idle(project: Proj) -> None:
    n = nxt(project)
    assert n["stage"] == "idle" and n["actor"] == "none"


def test_design_stages_advance_as_artifacts_appear(project: Proj) -> None:
    project.write(SDD_REL, "# SDD: Rate limiter\n\n## Overview\nx\n")
    assert nxt(project)["stage"] == "hld" and nxt(project)["skill"] == "design"

    stage_files(project)
    assert nxt(project)["stage"] == "hardened"  # complete and hardened, not yet frozen
    tool("design", project, "freeze")
    n = nxt(project)
    assert n["stage"] == "frozen" and n["skill"] == "lld"
    tool("design", project, "stamp-lld")
    n = nxt(project)
    assert n["stage"] == "design-ready" and n["skill"] == "breakdown" and SDD_REL in n["next"]


def test_a_proposed_adr_is_the_users_decision(project: Proj) -> None:
    stage_files(project)
    project.write("docs/adr/0001-redis-for-counters.md", ADR.replace("- Status: accepted", "- Status: proposed"))
    n = nxt(project)
    assert n["stage"] == "decisions" and n["actor"] == "user"


def test_editing_a_frozen_design_routes_to_redesign(project: Proj) -> None:
    stage_files(project)
    tool("design", project, "freeze")
    tool("design", project, "stamp-lld")
    p = project.root / SDD_REL
    p.write_text(p.read_text().replace("fail open by default", "fail closed by default"))
    n = nxt(project)
    assert n["stage"] == "design-drift" and n["skill"] == "redesign"


def test_plan_lifecycle(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan(approved=False))
    n = nxt(project)
    assert n["stage"] == "awaiting-approval" and n["actor"] == "user"

    project.write(".claude/task_state.json", plan(approved=True, statuses=("completed", "pending")))
    n = nxt(project)
    assert n["stage"] == "implementing" and n["actor"] == "claude" and n["detail"]["task"] == "2"

    state = plan(approved=True, statuses=("completed", "pending"))
    state["tasks"][1]["needs_recheck"] = True
    project.write(".claude/task_state.json", state)
    assert "needs re-planning" in nxt(project)["reason"]

    project.write(".claude/task_state.json", plan(approved=True, statuses=("completed", "completed")))
    project.init_git()
    project.write("app/x.py", "x = 1\n")
    assert nxt(project)["stage"] == "closing-docs"  # no final reconcile yet

    cfg = project.read(".claude/framework.json")
    cfg["reconcile"] = False
    project.write(".claude/framework.json", cfg)
    assert nxt(project)["stage"] == "retro"
    tool("flow", project, "mark", "retro")
    n = nxt(project)
    assert n["stage"] == "ship" and n["actor"] == "user"  # shipping is never taken for the user
    tool("flow", project, "mark", "shipped")
    assert nxt(project)["stage"] == "done"


def test_marks_belong_to_one_plan(project: Proj, plan) -> None:
    cfg = project.read(".claude/framework.json")
    cfg["reconcile"] = False
    project.write(".claude/framework.json", cfg)
    project.write(".claude/task_state.json", plan(approved=True, statuses=("completed", "completed"), tasks_created_at="2026-01-01T00:00:00+00:00"))
    tool("flow", project, "mark", "retro")
    tool("flow", project, "mark", "shipped")
    assert nxt(project)["stage"] == "done"
    project.write(".claude/task_state.json", plan(approved=True, statuses=("completed", "completed"), tasks_created_at="2026-02-01T00:00:00+00:00"))
    assert nxt(project)["stage"] == "retro"  # a new plan starts the closing steps again


def test_design_change_outranks_everything(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan(approved=True))
    subprocess.run([sys.executable, str(ROOT / "tools" / "design_change.py"), "open", "--title", "x", "--trigger", "y", "--project", str(project.root)],
                   check=True, capture_output=True, env=project.env)
    n = nxt(project)
    assert n["stage"] == "design-change" and n["skill"] == "redesign" and n["actor"] == "claude"
    subprocess.run([sys.executable, str(ROOT / "tools" / "design_change.py"), "propose", "DC-1", "--project", str(project.root)],
                   check=True, capture_output=True, env=project.env)
    assert nxt(project)["actor"] == "user"


def test_open_work_item_is_the_next_step(project: Proj) -> None:
    subprocess.run([sys.executable, str(ROOT / "tools" / "work.py"), "open", "--title", "Fix export", "--lane", "bug", "--project", str(project.root)],
                   check=True, capture_output=True, env=project.env)
    n = nxt(project)
    assert n["stage"] == "work-item" and "W-1" in n["next"]


def test_legacy_projects_without_the_readiness_gate_still_advance(project: Proj) -> None:
    cfg = project.read(".claude/framework.json")
    cfg["design_readiness_gate"] = False
    project.write(".claude/framework.json", cfg)
    stage_files(project)
    assert nxt(project)["stage"] == "design-ready"


# ------------------------------------------------------------------ surfaces

def test_session_start_and_stop_show_the_next_step(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan(approved=True))
    assert "[NEXT] implementing" in project.hook("session_start").stdout
    out = project.hook_json("stop_checklist", {"hook_event_name": "Stop"})
    assert "Next: implementing" in out.get("systemMessage", "")


def test_autopilot_off_is_silent_and_on_nudges_only_when_claude_moves(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan(approved=True))
    cfg = project.read(".claude/framework.json")
    cfg["autopilot"] = "off"
    project.write(".claude/framework.json", cfg)
    assert "Next:" not in project.hook_json("stop_checklist", {"hook_event_name": "Stop"}).get("systemMessage", "")

    cfg["autopilot"] = "on"
    project.write(".claude/framework.json", cfg)
    out = project.hook_json("stop_checklist", {"hook_event_name": "Stop"})
    assert out["decision"] == "block" and "takshak:sdd-implementer" in out["reason"]

    project.write(".claude/task_state.json", plan(approved=False))  # a human gate: autopilot must not push
    out = project.hook_json("stop_checklist", {"hook_event_name": "Stop", "stop_hook_active": False})
    assert out.get("decision") != "block"


def test_cli_prints_a_line_and_json(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan(approved=False))
    assert tool("flow", project, "next").stdout.startswith("[NEXT] awaiting-approval")
    assert json.loads(tool("flow", project, "next", "--json").stdout)["actor"] == "user"
