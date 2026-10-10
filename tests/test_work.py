"""Work items: lane hints, ticket sizing against the project's real issue types, the
confirm-then-auto Jira profile, and the gate that stops un-attributed edits (including Bash)."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from conftest import ROOT, Proj

sys.path.insert(0, str(ROOT / "hooks"))
import _work as work  # noqa: E402

TOOL = ROOT / "tools" / "work.py"

JIRA_TYPES = [
    {"name": "Epic", "hierarchyLevel": 1},
    {"name": "Story", "hierarchyLevel": 0},
    {"name": "Task", "hierarchyLevel": 0},
    {"name": "Bug", "hierarchyLevel": 0},
    {"name": "Subtask", "subtask": True, "hierarchyLevel": -1},
]


def tool(project: Proj, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    r = subprocess.run([sys.executable, str(TOOL), *args, "--project", str(project.root)],
                       capture_output=True, text=True, env=project.env, timeout=60, check=False)
    if check:
        assert r.returncode == 0, r.stderr
    return r


def tj(project: Proj, *args: str) -> dict:
    return json.loads(tool(project, *args).stdout)


def gate(project: Proj, tool_name: str, tool_input: dict) -> dict:
    return project.hook_json("approval_gate", {"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": tool_input})


def denied(result: dict) -> bool:
    return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def enable(project: Proj, **extra) -> None:
    cfg = project.read(".claude/framework.json")
    cfg.update({"work_item_gate": True, **extra})
    project.write(".claude/framework.json", cfg)


def src(project: Proj, rel: str = "src/app.py") -> dict:
    return {"file_path": str(project.root / rel), "content": "x = 1\n"}


def discover(project: Proj, types=None, key: str = "ABC") -> dict:
    return tj(project, "profile", "set", "--project-key", key, "--issue-types-json", json.dumps(types or JIRA_TYPES))


# ------------------------------------------------------------------ lane hint

@pytest.mark.parametrize(("text", "lane"), [
    ("how does the retry logic work?", "question"),
    ("explain the auth flow", "question"),
    ("the export crashes with a 500 on large files", "bug"),
    ("fix the typo in the README heading wording", "trivial"),
    ("add a new endpoint that lists invoices", "feature"),
    ("rename the helper and update its callers", "change"),
    ("build the new multi-region platform from scratch", "solution"),
])
def test_classify_lane(text: str, lane: str) -> None:
    assert work.classify(text)["lane"] == lane


def test_classify_ignores_slash_commands_and_empty() -> None:
    assert work.classify("/takshak:design foo")["lane"] == "question"
    assert work.classify("")["lane"] == "question"


# ------------------------------------------------------------------ sizing -> ticket type

def profile(types=None, mapping=None) -> dict:
    types = types or JIRA_TYPES
    return {"issue_types": types, "mapping": mapping or work.guess_mapping(types)}


def test_type_follows_the_size_of_the_work() -> None:
    p = profile()
    assert work.decide("solution", {"deliverables": 3}, p)["issue_type"] == "Epic"
    assert work.decide("feature", {"files": 4}, p)["issue_type"] == "Story"
    assert work.decide("bug", {"files": 1}, p)["issue_type"] == "Bug"
    assert work.decide("change", {"files": 2}, p)["issue_type"] == "Task"
    assert work.decide("trivial", {"files": 1}, p, "ABC-1")["issue_type"] == "Subtask"


def test_small_edit_without_a_parent_is_promoted_to_task() -> None:
    d = work.decide("trivial", {"files": 1}, profile(), None)
    assert d["issue_type"] == "Task"
    assert d["promoted_from"] == "subtask"


def test_ambiguity_rounds_up() -> None:
    p = profile()
    # a "tweak" that changes an API contract and needs a decision is not a subtask
    assert work.decide("trivial", {"files": 1, "contract": True}, p, "ABC-1")["level"] == "task"
    assert work.decide("trivial", {"files": 1, "contract": True, "adr": True}, p, "ABC-1")["level"] == "task"
    # a bug that sprawls across the codebase is tracked as more than a bug fix
    big = work.decide("bug", {"files": 9, "contract": True}, p)
    assert big["issue_type"] == "Story"
    assert not big["is_bug"]
    # a feature that is really several deliverables becomes an Epic
    assert work.decide("feature", {"files": 20, "deliverables": 2}, p)["issue_type"] == "Epic"


def test_uses_the_projects_real_type_names_and_falls_back() -> None:
    renamed = [{"name": "Epic", "hierarchyLevel": 1}, {"name": "Story"}, {"name": "Work item"},
               {"name": "Sub-task", "subtask": True}]
    p = profile(renamed)
    assert p["mapping"]["task"] == "Work item" and p["mapping"]["subtask"] == "Sub-task"
    assert work.decide("change", {}, p)["issue_type"] == "Work item"
    # no Bug type in this project: a bug is filed as the Task equivalent, and the reason says so
    bug = work.decide("bug", {}, p)
    assert bug["issue_type"] == "Work item"
    assert any("no 'bug' type" in r for r in bug["reasons"])
    # no Epic type at all: the largest thing falls back to Story
    no_epic = profile([{"name": "Story"}, {"name": "Task"}])
    assert work.decide("solution", {"deliverables": 3}, no_epic)["issue_type"] == "Story"


def test_hierarchy_metadata_names_a_renamed_epic() -> None:
    m = work.guess_mapping([{"name": "Initiative-ish", "hierarchyLevel": 1}, {"name": "Story"}])
    assert m["epic"] == "Initiative-ish"


# ------------------------------------------------------------------ confirm-then-auto profile

def test_profile_lifecycle_confirm_then_auto(project: Proj) -> None:
    assert tj(project, "profile", "show")["mode"] == "none"

    prof = discover(project)
    assert prof["mode"] == "unconfirmed"
    assert prof["mapping"]["bug"] == "Bug"
    # nothing is written to the project's config until the human has confirmed
    assert project.read(".claude/framework.json").get("jira_project_key") is None

    plan = tj(project, "ticket-plan", "--lane", "bug", "--files", "2")
    assert plan["ask_user"] is True and plan["profile_mode"] == "unconfirmed"

    confirmed = tj(project, "profile", "confirm")  # confirmed, but auto-mode not granted
    assert confirmed["mode"] == "confirm"
    cfg = project.read(".claude/framework.json")
    assert cfg["jira_integration"] is True and cfg["jira_project_key"] == "ABC"
    assert tj(project, "ticket-plan", "--lane", "bug")["ask_user"] is True  # still asks every time

    assert tj(project, "profile", "auto", "on")["mode"] == "auto"
    plan = tj(project, "ticket-plan", "--lane", "bug")
    assert plan["ask_user"] is False and plan["issue_type"] == "Bug" and plan["project_key"] == "ABC"
    assert (project.root / ".claude" / "jira_profile.json").is_file()  # remembered for the team


def test_profile_confirm_with_corrections_and_auto_flag(project: Proj) -> None:
    discover(project)
    out = tj(project, "profile", "confirm", "--project-key", "XYZ", "--mapping-json", '{"bug": "Task"}', "--auto")
    assert out["mode"] == "auto" and out["project_key"] == "XYZ"
    assert tj(project, "ticket-plan", "--lane", "bug")["issue_type"] == "Task"
    assert project.read(".claude/framework.json")["jira_project_key"] == "XYZ"


def test_rediscovery_drops_confirmation(project: Proj) -> None:
    discover(project)
    tj(project, "profile", "confirm", "--auto")
    again = discover(project, JIRA_TYPES[:4])  # the project changed shape (no Subtask type)
    assert again["mode"] == "unconfirmed" and again["auto"] is False


def test_confirm_requires_discovery_and_auto_requires_confirmation(project: Proj) -> None:
    assert tool(project, "profile", "confirm", check=False).returncode == 1
    discover(project)
    assert tool(project, "profile", "auto", "on", check=False).returncode == 1


def test_subtask_parent_comes_from_branch_or_active_ticket(project: Proj) -> None:
    project.init_git()
    project.commit("init")
    project.git("checkout", "-q", "-b", "feature/ABC-42-export")
    discover(project)
    tj(project, "profile", "confirm")
    plan = tj(project, "ticket-plan", "--lane", "trivial", "--files", "1")
    assert plan["issue_type"] == "Subtask" and plan["parent_key"] == "ABC-42"

    tj(project, "open", "--title", "Export", "--lane", "change", "--jira", "ABC-77", "--jira-type", "Task")
    assert tj(project, "ticket-plan", "--lane", "trivial")["parent_key"] == "ABC-77"  # the active item wins


# ------------------------------------------------------------------ ledger

def test_ledger_open_link_defer_switch_done(project: Proj) -> None:
    enable(project, jira_integration=True)
    a = tj(project, "open", "--title", "Fix export", "--lane", "bug")
    assert a["id"] == "W-1" and a["ticket_state"] == "missing"
    assert tj(project, "link", "W-1", "ABC-5")["jira_key"] == "ABC-5"
    b = tj(project, "open", "--title", "Tweak copy", "--lane", "trivial")
    assert b["id"] == "W-2"
    assert tj(project, "status", "--json")["active"]["id"] == "W-2"
    assert tj(project, "switch", "W-1")["id"] == "W-1"
    tj(project, "defer", "W-2", "--reason", "jira down")
    assert work.ticket_state(_project(project), work.find(work.load(_project(project)), "W-2")) == "deferred"
    assert tj(project, "done")["status"] == "done"
    assert tj(project, "status", "--json")["active"]["id"] == "W-2"  # falls back to the other open item
    assert tool(project, "link", "W-99", "ABC-1", check=False).returncode == 1


def _project(p: Proj):
    from _common import Project
    return Project(p.root)


# ------------------------------------------------------------------ the gate

def test_edit_blocked_until_a_work_item_is_open(project: Proj) -> None:
    enable(project)
    assert denied(gate(project, "Write", src(project)))
    assert "takshak:work" in gate(project, "Write", src(project))["hookSpecificOutput"]["permissionDecisionReason"]
    tj(project, "open", "--title", "Fix export", "--lane", "bug")
    assert not denied(gate(project, "Write", src(project)))
    tj(project, "done")
    assert denied(gate(project, "Write", src(project)))


def test_docs_and_planning_artifacts_never_need_a_work_item(project: Proj) -> None:
    enable(project)
    for rel in ("README.md", "docs/sdd/x.md", ".claude/notes.json", "docs/adr/0001-queue.md"):
        assert not denied(gate(project, "Write", src(project, rel))), rel


def test_with_jira_on_a_ticket_must_be_linked_or_deferred(project: Proj) -> None:
    enable(project, jira_integration=True)
    tj(project, "open", "--title", "Fix export", "--lane", "bug")
    blocked = gate(project, "Write", src(project))
    assert denied(blocked) and "no Jira ticket" in blocked["hookSpecificOutput"]["permissionDecisionReason"]
    tj(project, "link", "W-1", "ABC-9")
    assert not denied(gate(project, "Write", src(project)))
    tj(project, "open", "--title", "Other", "--lane", "change")
    assert denied(gate(project, "Write", src(project)))
    tj(project, "defer", "W-2", "--reason", "mcp not connected")  # Jira trouble never blocks the work
    assert not denied(gate(project, "Write", src(project)))


def test_an_approved_plan_with_open_tasks_is_its_own_work_item(project: Proj, plan) -> None:
    enable(project, jira_integration=True)
    project.write(".claude/task_state.json", plan(approved=True))
    assert not denied(gate(project, "Write", src(project)))
    done = plan(approved=True, statuses=("completed", "completed"))
    project.write(".claude/task_state.json", done)
    assert denied(gate(project, "Write", src(project)))  # a follow-up request is new work


def test_plan_awaiting_approval_still_blocks_even_with_a_work_item(project: Proj, plan) -> None:
    enable(project)
    tj(project, "open", "--title", "Hotfix", "--lane", "bug")
    project.write(".claude/task_state.json", plan(approved=False))
    assert "Plan awaiting approval" in gate(project, "Write", src(project))["hookSpecificOutput"]["permissionDecisionReason"]


def test_gate_can_be_turned_off(project: Proj) -> None:
    enable(project, work_item_gate=False)
    assert not denied(gate(project, "Write", src(project)))


# ------------------------------------------------------------------ Bash

@pytest.mark.parametrize("command", [
    "echo 'x = 1' > src/app.py",
    "cat >> src/app.py <<EOF\nx = 1\nEOF",
    "sed -i 's/a/b/' src/app.py",
    "cp /tmp/new.py src/app.py",
    "printf x | tee src/app.py",
    "python3 -c \"open('src/app.py','w').write('x')\"",
    "git apply fix.patch",
])
def test_bash_source_writes_need_a_work_item(project: Proj, command: str) -> None:
    enable(project)
    assert denied(gate(project, "Bash", {"command": command}))
    tj(project, "open", "--title", "Fix", "--lane", "bug")
    assert not denied(gate(project, "Bash", {"command": command}))


@pytest.mark.parametrize("command", [
    "ls -la", "pytest -q 2>&1 | tee /tmp/out.txt", "echo hi > /dev/null", "echo done > notes.md",
    "grep -r 'a > b' src", "echo \"a > b\"", "git status", "make build 2>&1",
])
def test_bash_that_does_not_write_source_is_left_alone(project: Proj, command: str) -> None:
    enable(project)
    assert not denied(gate(project, "Bash", {"command": command}))


def test_bash_cannot_approve_the_plan(project: Proj, plan) -> None:
    enable(project)
    project.write(".claude/task_state.json", plan(approved=False))
    cmd = """sed -i 's/"approved": false/"approved": true/' .claude/task_state.json"""
    assert denied(gate(project, "Bash", {"command": cmd}))


def test_bash_write_targets_unit() -> None:
    assert work.bash_write_targets("echo a > x.py && ls > /dev/null") == ["x.py"]
    assert work.bash_write_targets("sed -i.bak -e 's/a/b/' a.py b.py") == ["a.py", "b.py"]
    assert work.bash_write_targets("npm test 2>&1") == []
    assert work.bash_write_targets("cat <<EOF > f.py\nif a > b:\nEOF") == ["f.py"]


# ------------------------------------------------------------------ surfaces

def test_prompt_points_a_code_request_at_the_work_skill(project: Proj) -> None:
    enable(project, jira_integration=True)
    out = project.hook("prompt_submit", {"prompt": "the export crashes with a 500 on large files"}).stdout
    assert "[WORK]" in out and "takshak:work" in out and "'bug'" in out and "Jira ticket" in out
    assert project.hook("prompt_submit", {"prompt": "how does the export work?"}).stdout.strip() == ""
    assert "[WORK]" not in project.hook("prompt_submit", {"prompt": "approved"}).stdout


def test_prompt_with_an_active_item_offers_to_switch(project: Proj) -> None:
    enable(project)
    tj(project, "open", "--title", "Fix export", "--lane", "bug", "--jira", "ABC-5")
    out = project.hook("prompt_submit", {"prompt": "also add a new endpoint for invoices"}).stdout
    assert "Active work item W-1" in out and "ABC-5" in out


def test_prompt_stays_quiet_inside_an_approved_plan(project: Proj, plan) -> None:
    enable(project)
    project.write(".claude/task_state.json", plan(approved=True))
    assert project.hook("prompt_submit", {"prompt": "implement the limiter middleware"}).stdout.strip() == ""


def test_session_start_states_the_rule_and_the_jira_mode(project: Proj) -> None:
    enable(project, jira_integration=True)
    out = project.hook("session_start").stdout
    assert "[WORK]" in out and "not been discovered yet" in out
    discover(project)
    tj(project, "profile", "confirm", "--auto")
    out = project.hook("session_start").stdout
    assert "auto-mode on" in out


def test_session_start_lists_items_still_owed_a_ticket(project: Proj) -> None:
    enable(project, jira_integration=True)
    tj(project, "open", "--title", "Offline fix", "--lane", "bug")
    tj(project, "defer", "W-1", "--reason", "no mcp")
    assert "waiting for a Jira ticket (W-1)" in project.hook("session_start").stdout
    tj(project, "link", "W-1", "ABC-3")
    assert "waiting for a Jira ticket" not in project.hook("session_start").stdout


def test_stop_hook_mentions_an_open_item_without_blocking(project: Proj) -> None:
    enable(project)
    tj(project, "open", "--title", "Fix export", "--lane", "bug", "--jira", "ABC-5")
    out = project.hook_json("stop_checklist", {"hook_event_name": "Stop"})
    assert out.get("decision") != "block"
    assert "W-1" in out.get("systemMessage", "") and "ABC-5" in out["systemMessage"]


def test_new_settings_are_defaults_and_state_is_gitignored() -> None:
    cfg = json.loads((ROOT / "templates" / "framework.json").read_text())
    assert cfg["work_item_gate"] is True
    assert "work_items.json" in (ROOT / "templates" / "claude.gitignore").read_text()
    assert "jira_profile.json" not in (ROOT / "templates" / "claude.gitignore").read_text()  # shared on purpose
