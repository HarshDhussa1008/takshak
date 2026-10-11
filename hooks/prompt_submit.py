"""UserPromptSubmit hook -- fires on every user message; plain stdout reaches Claude.

1. Approval capture. When a plan is awaiting approval and the *user* replies "approved"
   (or "approve" / "lgtm"), this hook -- not Claude -- flips task_state.approved. Paired
   with approval_gate.py, which denies Claude's own attempts to flip it, the human
   checkpoint between design and code is enforced rather than requested.
2. Work-lane hint. A request that asks for a code change while no work item is open gets a
   one-line pointer to /takshak:work (the PreToolUse gate then enforces it), so ad-hoc
   requests enter the same pipeline instead of bypassing it.
3. Budget relay fallback. If the statusline tripped the budget alert and the Stop hook
   never got to deliver it, deliver it here.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _design_change as design_change  # noqa: E402
import _work as work  # noqa: E402
from _common import (  # noqa: E402
    Project,
    age_seconds,
    now_iso,
    project_root,
    read_json,
    read_payload,
    record_metric,
    run_safely,
    write_json,
)

APPROVAL_RE = re.compile(r"^\s*(approved?|lgtm)\b", re.IGNORECASE)


def capture_approval(project: Project, prompt: str) -> str | None:
    if not APPROVAL_RE.match(prompt or ""):
        return None
    if changes := design_change.approve_proposed(project):
        ids = ", ".join(c["id"] for c in changes)
        record_metric(project, "design_change_approved", ids=[c["id"] for c in changes])
        return (
            f"[APPROVAL] The user approved design change {ids}. Apply it now: update the SDD, tasks and tests to the "
            "approved decision (/takshak:redesign step 8), then run `design_change.py apply`. Source edits are unblocked "
            "only after it is applied."
        )
    state = read_json(project.task_state)
    if not state.get("tasks") or state.get("approved") is True:
        return None
    state["approved"] = True
    state["approved_at"] = now_iso()
    state["approved_by"] = "user-prompt"
    write_json(project.task_state, state)
    latency = age_seconds(state.get("tasks_created_at"))
    record_metric(project, "approval", task_count=len(state["tasks"]),
                  latency_seconds=round(latency) if latency is not None else None)
    return (
        f"[APPROVAL] The user approved the plan ({len(state['tasks'])} tasks). "
        "task_state.approved is now true -- implementation may begin."
    )


def budget_directive(project: Project) -> str | None:
    budget = read_json(project.budget)
    alert = budget.get("alert") or {}
    if not alert.get("tripped") or alert.get("acknowledged"):
        return None
    alert["acknowledged"] = True
    budget["alert"] = alert
    write_json(project.budget, budget)
    return (
        f"[BUDGET] {float(alert.get('used_percentage', 0)):.0f}% of the {alert.get('window')} limit "
        f"(threshold {alert.get('threshold')}%), resets {alert.get('resets_at_iso') or 'unknown'}. "
        "Write .claude/checkpoint.json NOW with the literal next action (file and line), finish only "
        "the edit in hand, and do not start new work."
    )


def lane_hint(project: Project, prompt: str) -> str | None:
    if not work.gate_enabled(project) or APPROVAL_RE.match(prompt or ""):
        return None
    hint = work.classify(prompt)
    if hint["lane"] == "question":
        return None
    item = work.active_item(project)
    if item is not None:
        return (
            f"[WORK] Active work item {item['id']} ({item['lane']}): {item['title']}"
            + (f" [{item['jira_key']}]" if item.get("jira_key") else "")
            + ". If this message is a different piece of work, run /takshak:work to open its own item "
            "(and ticket) instead of folding it in."
        )
    if work.plan_covers_edits(project):
        return None
    return (
        f"[WORK] This reads as a '{hint['lane']}' request ({'; '.join(hint['reasons'])}) and no work item "
        "is open. Before editing, invoke the takshak:work skill: it opens a work item"
        + (" and a Jira ticket sized to the work" if work.jira_on(project) else "")
        + f", then follow the {hint['lane']} lane. Source edits are blocked until it has."
        + (" For a feature or solution, hand off to /takshak:design rather than coding directly."
           if hint["lane"] in ("feature", "solution") else "")
    )


def main() -> None:
    payload = read_payload()
    project = Project(project_root(payload))
    if not project.enabled:
        return
    prompt = str(payload.get("prompt", ""))
    out = [msg for msg in (budget_directive(project), capture_approval(project, prompt), lane_hint(project, prompt)) if msg]
    if out:
        print("\n".join(out))


if __name__ == "__main__":
    run_safely(main)
