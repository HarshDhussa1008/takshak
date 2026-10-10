"""ADRs, requirement traceability, and the design-change loop."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from conftest import ROOT, Proj

sys.path.insert(0, str(ROOT / "hooks"))
import _trace as trace  # noqa: E402
from _common import Project  # noqa: E402

SDD = """# SDD: rate limit

## Requirements
| ID | Requirement | Acceptance criterion | Priority |
|----|-------------|----------------------|----------|
| R1 | Reject requests over the tenant quota | 429 after N calls in a window | Must |
| R2 | Quota is configurable per tenant | Config change applies without restart | Must |
| NFR1 | Limiter adds under 5 ms p99 | benchmark < 5 ms | Must |
| R9 | Admin UI for quotas | n/a | Won't (later) |

## Test Plan
| Req | Test | Type |
|-----|------|------|
"""


def tool(name: str, project: Proj, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    r = subprocess.run([sys.executable, str(ROOT / "tools" / f"{name}.py"), *args, "--project", str(project.root)],
                       capture_output=True, text=True, env=project.env, timeout=60, check=False)
    if check:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


def gate(project: Proj, tool_name: str, tool_input: dict) -> dict:
    return project.hook_json("approval_gate", {"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": tool_input})


def denied(result: dict) -> bool:
    return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def tasks_state(plan, **req):
    state = plan(approved=True, sdd_path="docs/sdd/rl.md")
    for t in state["tasks"]:
        t["requirements"] = req.get(t["id"], [])
    return state


@pytest.fixture
def traced(project: Proj, plan) -> Proj:
    project.write("docs/sdd/rl.md", SDD)
    project.write(".claude/task_state.json", tasks_state(plan, **{"1": ["R1", "NFR1"], "2": ["R2"]}))
    return project


# ------------------------------------------------------------------ traceability

def test_requirements_table_is_parsed_by_header_name() -> None:
    reqs = trace.parse_requirements(SDD)
    assert [r["id"] for r in reqs] == ["R1", "R2", "NFR1", "R9"]
    assert reqs[0]["acceptance"].startswith("429")
    assert trace.parse_requirements("# no table here") == []


def test_plan_check_passes_when_every_requirement_has_a_task(traced: Proj) -> None:
    out = tool("trace", traced, "check", "--mode", "plan")
    assert "unplanned" in out.stdout


def test_requirement_without_a_task_and_task_without_a_requirement_fail(traced: Proj, plan) -> None:
    traced.write(".claude/task_state.json", tasks_state(plan, **{"1": ["R1", "NFR1"]}))  # task 2 traces to nothing; R2 unplanned
    out = tool("trace", traced, "check", "--mode", "plan", check=False)
    assert out.returncode == 1
    assert "R2 has no task" in out.stdout and "task 2 traces to no requirement" in out.stdout


def test_unknown_requirement_reference_is_flagged(traced: Proj, plan) -> None:
    traced.write(".claude/task_state.json", tasks_state(plan, **{"1": ["R1", "NFR1", "R42"], "2": ["R2"]}))
    out = tool("trace", traced, "check", "--mode", "plan", check=False)
    assert out.returncode == 1 and "R42" in out.stdout


def test_ship_mode_needs_a_covers_marked_test(traced: Proj) -> None:
    out = tool("trace", traced, "check", "--mode", "ship", check=False)
    assert out.returncode == 1 and "R1 has no test" in out.stdout and "NFR1 has no test" in out.stdout
    assert "R9" not in out.stdout  # deferred ("Won't (later)") is not owed a test
    traced.write("tests/test_limit.py", "# covers: R1, NFR1\ndef test_a():\n    pass\n")
    traced.write("tests/test_cfg.js", "// covers: R2\n")
    assert tool("trace", traced, "check", "--mode", "ship").returncode == 0


def test_decorator_style_markers_count(traced: Proj) -> None:
    traced.write("tests/test_x.py", '@covers("R1")\ndef test_a(): ...\n# covers: NFR1; R2\n')
    report = trace.build(Project(traced.root))
    assert {r["id"]: r["status"] for r in report["requirements"]}["R1"] == "covered"
    assert {r["id"]: r["status"] for r in report["requirements"]}["R2"] == "covered"


def test_legacy_sdd_without_ids_is_not_failed(project: Proj, plan) -> None:
    project.write("docs/sdd/rl.md", "# SDD\n\n## Goals\n- fast\n")
    project.write(".claude/task_state.json", plan(approved=True, sdd_path="docs/sdd/rl.md"))
    assert tool("trace", project, "check", "--mode", "ship").returncode == 0


# ------------------------------------------------------------------ ADRs

def new_adr(project: Proj, title: str, reqs: str = "R1") -> str:
    return tool("adr", project, "new", "--title", title, "--requirements", reqs).stdout.strip()


def fill(project: Proj, rel: str) -> None:
    path = project.root / rel
    text = path.read_text()
    text = text.replace("<The forces at play: the requirement, the constraint, what makes this a real decision. Facts, not advocacy.>",
                        "The limiter must survive restarts without double counting calls.")
    text = text.replace("<How it works, what it costs, where it breaks.>", "Counts live in Redis with a Lua script; costs a network hop.", 1)
    text = text.replace("<How it works, what it costs, where it breaks.>", "Counts live in process memory; lost on restart.", 1)
    text = text.replace("<The option chosen, in one or two sentences, and the single deciding reason.>", "Option A, because counts must survive restarts.")
    text = text.replace("<what gets easier or safer>", "restart-safe").replace("<what we are accepting; what gets harder>", "extra dependency")
    text = text.replace("<the observable condition that should reopen this decision>", "p99 limiter latency above 5 ms")
    path.write_text(text)


def test_adrs_are_numbered_by_the_tool(project: Proj) -> None:
    a = new_adr(project, "Redis for counters")
    b = new_adr(project, "Queue over cron")
    assert a == "docs/adr/0001-redis-for-counters.md" and b == "docs/adr/0002-queue-over-cron.md"
    assert "Requirements: R1" in (project.root / a).read_text()


def test_unfilled_template_and_single_option_fail_the_check(project: Proj) -> None:
    rel = new_adr(project, "Redis for counters")
    out = tool("adr", project, "check", check=False)
    assert out.returncode == 1 and "template placeholder" in out.stdout
    fill(project, rel)
    assert tool("adr", project, "check").returncode == 0
    text = (project.root / rel).read_text()
    (project.root / rel).write_text(text.replace("### Option B", "### Notes"))
    out = tool("adr", project, "check", check=False)
    assert out.returncode == 1 and "at least two" in out.stdout


def test_supersede_links_both_records_and_check_agrees(project: Proj) -> None:
    old, new = new_adr(project, "Redis for counters"), new_adr(project, "Postgres for counters")
    fill(project, old)
    fill(project, new)
    tool("adr", project, "accept", "2")
    tool("adr", project, "supersede", "1", "--by", "2")
    assert "superseded by ADR-0002" in (project.root / old).read_text()
    assert "Supersedes: ADR-0001" in (project.root / new).read_text()
    assert tool("adr", project, "check").returncode == 0
    listing = json.loads(tool("adr", project, "list", "--json").stdout)
    assert [a["status"] for a in listing] == ["superseded by ADR-0002", "accepted"]


def test_adr_numbering_gap_and_unknown_requirement_are_caught(traced: Proj) -> None:
    rel = new_adr(traced, "Redis for counters", reqs="R77")
    fill(traced, rel)
    out = tool("adr", traced, "check", check=False)
    assert out.returncode == 1 and "unknown requirement R77" in out.stdout
    (traced.root / rel).rename(traced.root / "docs/adr/0003-redis.md")
    assert "numbering" in tool("adr", traced, "check", check=False).stdout


def test_adr_filename_convention_is_enforced(project: Proj) -> None:
    project.write("docs/adr/redis.md", "# Redis\n")
    out = tool("adr", project, "check", check=False)
    assert out.returncode == 1 and "NNNN-kebab-title.md" in out.stdout


# ------------------------------------------------------------------ design change loop

def src(project: Proj) -> dict:
    return {"file_path": str(project.root / "src/app.py"), "content": "x = 1\n"}


def test_open_design_change_blocks_source_but_not_planning(traced: Proj) -> None:
    assert not denied(gate(traced, "Write", src(traced)))
    tool("design_change", traced, "open", "--title", "Counters race", "--trigger", "two workers double count", "--task", "1")
    out = gate(traced, "Write", src(traced))
    assert denied(out) and "DC-1" in out["hookSpecificOutput"]["permissionDecisionReason"]
    assert denied(gate(traced, "Bash", {"command": "echo x > src/app.py"}))
    for rel in ("docs/sdd/rl.md", "docs/adr/0001-x.md", ".claude/task_state.json"):
        assert not denied(gate(traced, "Write", {"file_path": str(traced.root / rel), "content": "x"})), rel


def test_user_approval_is_captured_and_claude_cannot_self_approve(traced: Proj) -> None:
    tool("design_change", traced, "open", "--title", "Counters race", "--trigger", "double count")
    tool("design_change", traced, "propose", "DC-1", "--adr", "2", "--tasks", "2", "--sections", "Data Models")
    assert traced.read(".claude/task_state.json")["tasks"][1]["needs_recheck"] is True

    path = str(traced.root / ".claude/design_changes.json")
    forged = json.loads((traced.root / ".claude/design_changes.json").read_text())
    forged["items"][0]["status"] = "approved"
    assert denied(gate(traced, "Write", {"file_path": path, "content": json.dumps(forged, indent=2)}))
    assert denied(gate(traced, "Edit", {"file_path": path, "old_string": '"status": "proposed"', "new_string": '"status": "approved"'}))
    assert denied(gate(traced, "Bash", {"command": f"sed -i 's/\"proposed\"/\"approved\"/' {path} && grep '\"status\": \"approved\"' {path}"}))
    r = tool("design_change", traced, "apply", "DC-1", check=False)
    assert r.returncode == 1  # not approved: apply refuses

    out = traced.hook("prompt_submit", {"prompt": "approved"}).stdout
    assert "approved design change DC-1" in out
    assert traced.read(".claude/design_changes.json")["items"][0]["status"] == "approved"
    assert denied(gate(traced, "Write", src(traced)))  # approved but not yet applied: still paused

    tool("design_change", traced, "apply", "DC-1")
    assert not denied(gate(traced, "Write", src(traced)))


def test_approval_goes_to_the_design_change_not_the_plan(project: Proj, plan) -> None:
    project.write(".claude/task_state.json", plan(approved=False))
    tool("design_change", project, "open", "--title", "x", "--trigger", "y")
    tool("design_change", project, "propose", "DC-1")
    project.hook("prompt_submit", {"prompt": "approved"})
    assert project.read(".claude/task_state.json")["approved"] is False
    assert project.read(".claude/design_changes.json")["items"][0]["status"] == "approved"


def test_reject_unblocks(traced: Proj) -> None:
    tool("design_change", traced, "open", "--title", "x", "--trigger", "y")
    tool("design_change", traced, "reject", "DC-1")
    assert not denied(gate(traced, "Write", src(traced)))


def test_surfaces_mention_the_open_change(traced: Proj) -> None:
    tool("design_change", traced, "open", "--title", "Counters race", "--trigger", "y")
    assert "[DESIGN CHANGE] DC-1" in traced.hook("session_start").stdout
    out = traced.hook_json("stop_checklist", {"hook_event_name": "Stop"})
    assert "DC-1" in out.get("systemMessage", "")


def test_gitignore_and_config_defaults() -> None:
    ignore = (ROOT / "templates" / "claude.gitignore").read_text()
    assert "design_changes.json" in ignore
    assert json.loads((ROOT / "templates" / "framework.json").read_text())["adr_path"] == "docs/adr"
