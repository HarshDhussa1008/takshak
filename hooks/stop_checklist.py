"""Stop hook -- runs when Claude finishes a response.

Two channels, chosen by who has to act:
- Claude must act (budget breach, staged amendments, stale checkpoint):
  {"decision": "block", "reason": ...} -- Claude continues and reads the reason.
  Guarded against loops: never when stop_hook_active is set, and never twice in a row
  for the same checklist.
- The user should know (open tasks, drift, uncommitted files, retro offer):
  a top-level systemMessage, emitted only when it changed since last time so it
  never becomes wallpaper.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    Project,
    age_seconds,
    emit,
    git,
    hook_state,
    project_root,
    read_json,
    read_payload,
    run_safely,
    save_hook_state,
    write_json,
)

MEMORY_NUDGE_HOURS = 4
CHECKPOINT_STALE_MINUTES = 10
DRIFT_THRESHOLD = 3
COMMON_WORDS = {"implement", "update", "change", "support", "handle", "create", "remove", "should", "which", "their"}


def digest(lines: list[str]) -> str:
    return hashlib.sha1("\n".join(lines).encode("utf-8")).hexdigest()[:12]


def budget_item(project: Project) -> str | None:
    alert = read_json(project.budget).get("alert") or {}
    if not alert.get("tripped") or alert.get("acknowledged"):
        return None
    return (
        f"BUDGET {float(alert.get('used_percentage', 0)):.0f}% of the {alert.get('window')} limit "
        f"(threshold {alert.get('threshold')}%), resets {alert.get('resets_at_iso') or 'unknown'}: write "
        ".claude/checkpoint.json NOW with the literal next action (file and line), finish only the edit "
        "in hand, and start no new work."
    )


def acknowledge_budget(project: Project) -> None:
    budget = read_json(project.budget)
    if alert := budget.get("alert"):
        alert["acknowledged"] = True
        write_json(project.budget, budget)


def amendment_items(project: Project) -> list[str]:
    data = read_json(project.amendments)
    items = []
    if candidates := data.get("candidates"):
        items.append(
            f"{len(candidates)} design-gap amendment(s) staged: append them to the SDD's Amendments "
            "section, set needs_recheck on affected pending tasks, then clear the candidates."
        )
    if styles := data.get("style_candidates"):
        items.append(f"{len(styles)} style correction(s) staged: write them as feedback memories, then clear.")
    return items


def checkpoint_item(project: Project) -> str | None:
    state = read_json(project.task_state)
    tasks = state.get("tasks") or []
    if state.get("approved") is not True or not any(t.get("status") in ("in_progress", "completed") for t in tasks):
        return None
    if all(t.get("status") == "completed" for t in tasks):
        return None
    if not read_json(project.checkpoint).get("active_skill"):
        return "Implementation is underway but .claude/checkpoint.json is empty: write it now (literal next action, file and line)."
    try:
        lag = project.task_state.stat().st_mtime - project.checkpoint.stat().st_mtime
    except OSError:
        return None
    if lag > CHECKPOINT_STALE_MINUTES * 60:
        return "checkpoint.json is stale relative to task_state.json: update phase and next_step."
    return None


def task_refs(task: dict[str, Any]) -> list[re.Pattern[str]]:
    refs = []
    if jira := task.get("jira_key"):
        refs.append(re.compile(rf"\b{re.escape(str(jira))}\b", re.IGNORECASE))
    if tid := task.get("id"):
        # Task IDs are short integers; a bare "1" matches almost any subject, so require a marker.
        refs.append(re.compile(rf"(?:\bT|#|\btask[ -]?)0*{re.escape(str(tid))}\b", re.IGNORECASE))
    words = [w for w in re.findall(r"[a-z]{5,}", str(task.get("title", "")).lower()) if w not in COMMON_WORDS]
    refs.extend(re.compile(rf"\b{re.escape(w)}") for w in words[:3])
    return refs


def drift_item(project: Project) -> str | None:
    state = read_json(project.task_state)
    tasks = state.get("tasks") or []
    if not tasks:
        return None
    args = ["log", "-15", "--format=%h %s"]
    if since := state.get("approved_at"):
        args.append(f"--since={since}")
    log = git(project.root, *args, "--", ".")
    patterns = [p for t in tasks for p in task_refs(t)]
    orphans = [ln for ln in log.splitlines() if " " in ln and not any(p.search(ln.split(" ", 1)[1]) for p in patterns)]
    if len(orphans) >= DRIFT_THRESHOLD:
        return f"{len(orphans)} recent commit(s) map to no planned task (scope drift, or task_state is stale)."
    return None


def retro_item(project: Project) -> str | None:
    state = read_json(project.task_state)
    tasks = state.get("tasks") or []
    if not tasks or state.get("retro_offered") or any(t.get("status") != "completed" for t in tasks):
        return None
    state["retro_offered"] = True
    write_json(project.task_state, state)
    return f"All {len(tasks)} task(s) complete. Run /takshak:retro while it is fresh."


def info_items(project: Project) -> list[str]:
    items = []
    if drift := drift_item(project):
        items.append(drift)
    dirty = [ln for ln in git(project.root, "status", "--porcelain", "--", ".").splitlines() if ln.strip()]
    dirty = [ln for ln in dirty if "/.claude/" not in f"/{ln[3:]}" and not ln[3:].startswith(".claude/")]
    if dirty:
        items.append(f"{len(dirty)} uncommitted file(s).")
    state = read_json(project.task_state)
    open_tasks = [str(t.get("id")) for t in state.get("tasks") or [] if t.get("status") in ("pending", "in_progress")]
    if open_tasks:
        items.append(f"{len(open_tasks)} task(s) open: {', '.join(open_tasks)}.")
    elif offer := retro_item(project):
        items.append(offer)
    registry = read_json(project.registry)
    unwritten = [f"{k} x{v.get('count')}" for k, v in registry.items() if v.get("count", 0) >= 2 and not v.get("memory_written")]
    if unwritten:
        items.append(f"Recurring lint/type patterns without a memory: {', '.join(unwritten[:5])}.")
    last_write = age_seconds(state.get("last_memory_write"))
    if state.get("tasks") and (last_write is None or last_write > MEMORY_NUDGE_HOURS * 3600):
        items.append("No memory written in 4h+: any non-obvious decision worth /takshak:remember?")
    return items


def main() -> None:
    payload = read_payload()
    project = Project(project_root(payload))
    if not project.enabled:
        return

    memo = hook_state(project)
    actions = [i for i in [budget_item(project), *amendment_items(project), checkpoint_item(project)] if i]
    info = info_items(project)

    action_hash = digest(actions) if actions else ""
    can_block = bool(actions) and not payload.get("stop_hook_active") and memo.get("last_block") != action_hash

    output: dict[str, Any] = {}
    if can_block:
        output["decision"] = "block"
        output["reason"] = "Takshak checklist -- handle before finishing:\n" + "\n".join(f"- {a}" for a in actions)
        acknowledge_budget(project)
        memo["last_block"] = action_hash
    elif actions:
        info = [f"still pending: {a}" for a in actions] + info  # already asked Claude once; tell the user
    else:
        memo.pop("last_block", None)

    info_hash = digest(info) if info else ""
    if info and memo.get("last_info") != info_hash:
        output["systemMessage"] = "Takshak: " + " | ".join(info)
    memo["last_info"] = info_hash

    save_hook_state(project, memo)
    if output:
        emit(output)


if __name__ == "__main__":
    run_safely(main)
