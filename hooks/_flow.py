"""The pipeline as a state machine: what is the next step, and who takes it?

Takshak has many stages (work item, design, ADRs, freeze, LLD, readiness, breakdown, approval,
implementation, reconcile, review, retro, ship, redesign). Asking a human to remember which
command comes next defeats the point. Everything needed to answer "what now?" is already on
disk, so this module derives it:

    next_step(project) -> {"stage", "next", "skill", "actor", "reason", "detail"}

`actor` says who must move: "claude" (invoke the skill), "user" (approve / decide / ship), or
"none" (idle). The SessionStart hook prints it as [NEXT]; the Stop hook shows it, and with
`autopilot: "on"` asks Claude to continue when the actor is Claude. /takshak:start is the one
command that follows it.
"""

from __future__ import annotations

from typing import Any

import _design as design
import _design_change as design_change
import _reconcile as reconcile
import _trace as trace
import _work as work
from _common import Project, now_iso, read_json, write_json

FLOW_NAME = "flow_state.json"
MARKS = ("retro", "shipped")


def flow_path(project: Project):  # type: ignore[no-untyped-def]
    return project.state_dir / FLOW_NAME


def plan_id(project: Project) -> str:
    return str(read_json(project.task_state).get("tasks_created_at") or "")


def mark(project: Project, what: str) -> dict[str, Any]:
    """Record that a once-per-plan step happened (review, retro, ship). Keyed to the current plan,
    so a new breakdown starts a fresh set."""
    data = read_json(flow_path(project))
    if data.get("plan") != plan_id(project):
        data = {"plan": plan_id(project)}
    data[what] = now_iso()
    write_json(flow_path(project), data)
    return data


def marks(project: Project) -> dict[str, Any]:
    data = read_json(flow_path(project))
    return data if data.get("plan") == plan_id(project) else {}


def step(stage: str, next_: str, skill: str | None, actor: str, reason: str, **detail: Any) -> dict[str, Any]:
    return {"stage": stage, "next": next_, "skill": skill, "actor": actor, "reason": reason, "detail": detail}


def design_step(project: Project) -> dict[str, Any] | None:
    sdd = trace.sdd_file(project)
    if sdd is None:
        return None
    rel = sdd.relative_to(project.root).as_posix()
    checks = design.readiness(project, rel)
    failed = {c["check"] for c in checks if not c["ok"]}
    fs = design.freeze_state(project, rel)
    if not project.config().get("design_readiness_gate", True):
        hardened = not design.hardening_problems(design.read(project, rel))
        return step("design-ready", f"/takshak:breakdown {rel}", "breakdown", "claude",
                    "readiness gate is off for this project; the design is hardened" if hardened else "readiness gate is off but the design is not hardened",
                    sdd=rel) if hardened else step("hld", "/takshak:design", "design", "claude", "the design is not hardened yet", sdd=rel)
    if not failed:
        return step("design-ready", f"/takshak:breakdown {rel}", "breakdown", "claude",
                    "design passes the readiness gate; break it into tasks", sdd=rel)
    if fs["frozen"] and not fs["intact"]:
        return step("design-drift", "/takshak:redesign", "redesign", "claude",
                    "the SDD or an accepted ADR changed after the freeze", sdd=rel)
    if not fs["frozen"]:
        proposed = [c for c in checks if c["check"] == "No ADR is still only proposed" and not c["ok"]]
        if proposed:
            return step("decisions", "decide the open ADRs", "design", "user", proposed[0]["detail"], sdd=rel)
        if "Adversarially hardened" in failed or "Decisions recorded as ADRs" in failed:
            return step("hld", "/takshak:design", "design", "claude", "the high-level design is not complete and hardened yet", sdd=rel, failing=sorted(failed))
        return step("hardened", "/takshak:design (freeze)", "design", "claude", "hardened: resolve open questions and freeze the design", sdd=rel, failing=sorted(failed))
    if {"LLD derived from the frozen design", "LLD specifies every module"} & failed:
        return step("frozen", "/takshak:lld", "lld", "claude", "the design is frozen; derive the low-level design", sdd=rel)
    return step("readiness", "/takshak:design (readiness)", "design", "claude", "fix the failing readiness checks", sdd=rel, failing=sorted(failed))


def next_step(project: Project) -> dict[str, Any]:
    pending = design_change.blocking(project)
    if pending:
        c = pending[0]
        if c["status"] == "proposed":
            return step("design-change", "approve or push back on the design change", "redesign", "user",
                        f"{c['id']} is waiting for your decision: {c['title']}", id=c["id"])
        return step("design-change", "/takshak:redesign", "redesign", "claude",
                    f"{c['id']} is {c['status']}: {c['title']}", id=c["id"])

    state = read_json(project.task_state)
    tasks = state.get("tasks") or []
    if tasks:
        if state.get("approved") is not True:
            return step("awaiting-approval", "review the design pack and the task plan, then reply approved", None, "user",
                        f"{len(tasks)} task(s) planned from {state.get('sdd_path') or 'the SDD'} (a peer can be walked through it with /takshak:review)")
        open_tasks = [t for t in tasks if t.get("status") != "completed"]
        if open_tasks:
            recheck = [t for t in open_tasks if t.get("needs_recheck")]
            t = (recheck or open_tasks)[0]
            return step("implementing", "/takshak:sdd-implementer", "sdd-implementer", "claude",
                        f"{len(open_tasks)} task(s) left; next is {t.get('id')}: {t.get('title')}" + (" (needs re-planning first)" if recheck else ""),
                        task=str(t.get("id")))
        info = reconcile.status(project) if reconcile.enabled(project) else {"final": True}
        if not info.get("final"):
            return step("closing-docs", "/takshak:reconcile final", "reconcile", "claude",
                        "all tasks are done; bring the Implementation Guide in line with the code")
        done = marks(project)
        if "retro" not in done:
            return step("retro", "/takshak:retro", "retro", "claude", "implementation is complete and documented; capture what the plan got wrong while it is fresh")
        if "shipped" not in done:
            return step("ship", "/takshak:ship staging", "ship", "user",
                        "documented and retro'd; shipping runs the gates, including the independent code review, and is your call")
        return step("done", "nothing: this plan is shipped", None, "none", "all stages are complete")

    item = work.active_item(project)
    if item is not None:
        return step("work-item", f"finish {item['id']} ({item['lane']}): {item['title']}", "work", "claude",
                    "a work item is open; complete it, then `tools/work.py done`", id=item["id"])

    d = design_step(project)
    if d is not None:
        return d
    return step("idle", "describe what you want changed or built", "start", "none", "nothing is in flight")


def line(info: dict[str, Any]) -> str:
    who = {"claude": "Claude continues with", "user": "waiting on you:", "none": ""}[info["actor"]]
    return f"[NEXT] {info['stage']}: {who} {info['next']} -- {info['reason']}".replace(":  ", ": ")
