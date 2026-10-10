"""Design changes: what happens when implementation proves the design wrong.

Implementation finds flaws -- a race the design missed, a contract that cannot work, a decision
that was simply wrong. The failure mode is quiet patching: the code drifts, the SDD becomes
fiction, and the next task builds on a foundation nobody re-checked. A design change makes the
loop explicit and gives it a human checkpoint:

    open      Claude has found a flaw; source edits stop (planning artifacts stay writable)
    proposed  the delta is written up (ADR, affected sections, affected tasks); waiting for the user
    approved  set ONLY by the user's own "approved" message (prompt_submit.py), never by Claude
    applied   the SDD, tasks and tests have been brought in line with the decision

State: .claude/design_changes.json (per developer, gitignored). The decision itself lives in an ADR.
"""

from __future__ import annotations

from typing import Any

from _common import Project, now_iso, read_json, write_json

STATE_NAME = "design_changes.json"
KINDS = ("flaw", "gap", "wrong-decision", "requirement-change")


def path(project: Project):  # type: ignore[no-untyped-def]
    return project.state_dir / STATE_NAME


def load(project: Project) -> dict[str, Any]:
    data = read_json(path(project))
    data.setdefault("counter", 0)
    data.setdefault("items", [])
    return data


def save(project: Project, data: dict[str, Any]) -> None:
    write_json(path(project), data)


def find(data: dict[str, Any], change_id: str) -> dict[str, Any] | None:
    return next((i for i in data["items"] if i["id"] == change_id), None)


def blocking(project: Project) -> list[dict[str, Any]]:
    """Changes that stop source edits: being analysed, waiting for the user, or approved but not
    yet applied to the SDD and task list (code must not be built on a design that is mid-correction)."""
    return [i for i in load(project)["items"] if i["status"] in ("open", "proposed", "approved")]


def open_change(project: Project, title: str, trigger: str, kind: str = "flaw", task_id: str | None = None) -> dict[str, Any]:
    data = load(project)
    data["counter"] += 1
    item = {
        "id": f"DC-{data['counter']}", "title": title.strip()[:200], "trigger": trigger.strip(),
        "kind": kind if kind in KINDS else "flaw", "status": "open", "found_in_task": task_id,
        "adr": None, "affected_tasks": [], "affected_sections": [], "opened_at": now_iso(),
        "proposed_at": None, "approved_at": None, "applied_at": None,
    }
    data["items"].append(item)
    save(project, data)
    return item


def propose(project: Project, change_id: str, adr: str | None, tasks: list[str], sections: list[str]) -> dict[str, Any] | None:
    data = load(project)
    item = find(data, change_id)
    if item is None or item["status"] not in ("open", "proposed"):
        return None
    item.update({"status": "proposed", "adr": adr, "affected_tasks": tasks, "affected_sections": sections,
                 "proposed_at": now_iso()})
    save(project, data)
    # Tasks whose plan the change may invalidate must be re-planned before they are implemented.
    state = read_json(project.task_state)
    if state.get("tasks") and tasks:
        for t in state["tasks"]:
            if str(t.get("id")) in tasks and t.get("status") != "completed":
                t["needs_recheck"] = True
        write_json(project.task_state, state)
    return item


def approve_proposed(project: Project) -> list[dict[str, Any]]:
    """Called by the UserPromptSubmit hook when the *user* says "approved"."""
    data = load(project)
    done = []
    for item in data["items"]:
        if item["status"] == "proposed":
            item["status"] = "approved"
            item["approved_at"] = now_iso()
            done.append(item)
    if done:
        save(project, data)
    return done


def apply(project: Project, change_id: str) -> dict[str, Any] | None:
    data = load(project)
    item = find(data, change_id)
    if item is None or item["status"] != "approved":
        return None
    item["status"] = "applied"
    item["applied_at"] = now_iso()
    save(project, data)
    return item


def reject(project: Project, change_id: str) -> dict[str, Any] | None:
    data = load(project)
    item = find(data, change_id)
    if item is None or item["status"] in ("applied", "rejected"):
        return None
    item["status"] = "rejected"
    save(project, data)
    return item
