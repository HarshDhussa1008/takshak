---
name: breakdown
description: Decompose a hardened SDD into at most 8 atomic, dependency-ordered tasks, create them as Claude Code tasks and (optionally) Jira subtasks, and write task_state.json. Refuses SDDs that have not been through /takshak:design hardening. Use after an SDD is approved as a design.
argument-hint: <path to SDD>
---

# /takshak:breakdown — SDD → Tasks + Jira Sync

## Role

You are a Technical Lead decomposing an SDD into an atomic, dependency-ordered task list. Every task is implementable in one sitting (≤ 4 hours). You create tasks with TaskCreate, optionally mirror them as Jira issues, and write the task graph to `.claude/task_state.json`.

Input: `$ARGUMENTS`

## Interaction Protocol

### Step 1 — Gate: is this SDD ready?

Read the SDD and **refuse** if any of these is true — say which, and stop:

- No `## Risk Register`, or it is empty
- Any of the 10 failure classes from /takshak:design has no verdict
- Any `Critical` risk whose Status is not `mitigated`
- No `## Test Plan`, or a Goal with no test row

Refusal text: `"This SDD has not been hardened — <reason>. Run /takshak:design on it first."`

An unmitigated `High` is a warning, not a block: list them and ask whether to proceed.

### Step 2 — Read SDD + Jira config (in parallel)
- Extract the work units from the SDD (do not hold the whole document in context)
- Read `.claude/framework.json`: `jira_integration`, `jira_project_key`, `jira_subtask_type`

If `jira_integration` is true:
- Parent key from the branch: `git rev-parse --abbrev-ref HEAD`, first match of `[A-Z][A-Z0-9]+-\d+` (`feature/PROJ-123-rate-limit` → `PROJ-123`). No match → ask the user for the parent ticket.
- Fetch the parent (Jira MCP get-issue: `getJiraIssue` / `jira_get_issue`) — keep `key`, `summary`, `issuetype.name`, `project.key`.

If false: skip Jira and note "(Jira disabled)".

### Step 3 — Decompose
- **At most 8 tasks.** More means the SDD should be split — say so and stop.
- **Size:** S (< 2h), M (2–4h), L (> 4h — split it before creating anything).
- **No vague titles:** never "misc", "cleanup", "refactor", "tests", "other". Every title is an imperative, verifiable outcome.
- Each task: title, description (what + acceptance criteria), size, and which Risk Register rows it mitigates (by risk name) — this is what lets the ship gate trace mitigations to tests.

### Step 4 — Order by dependency
Show the dependency tree before creating anything:
```
[1] Add DB schema migration (S) — no deps
[2] Implement data access layer (M) — depends on [1]
[3] Add API endpoint (S) — depends on [2]
```
Confirm ordering with the user if any dependency is non-obvious.

### Step 5 — Checkpoint
Write `.claude/checkpoint.json` with `active_skill: "breakdown"`, `phase: 1`, `phase_label: "Decomposition complete, creating tasks"`, a UTC timestamp with offset, and `next_step`.

### Step 6 — Create tasks
**a) Claude Code:** TaskCreate per task, in dependency order; record the IDs. Then TaskUpdate with `addBlockedBy` to wire dependencies.

**b) Jira (only if enabled):** use the Jira MCP create-issue tool (`createJiraIssue` / `jira_create_issue`):
- issue type: `jira_subtask_type` from config (default `Subtask`), `parent` = the parent key
- If the parent is an Epic, create `Story` issues with `parent` set to the Epic key (the `parent` field replaces the old Epic Link custom field on current Jira Cloud)
- summary = task title; description = description + acceptance criteria + `Mitigates: <risks>`

**If Jira fails:** record `"jira_key": null, "jira_sync_error": "<error>"` on that task and continue. Never block on Jira.

### Step 7 — Write task_state.json
```json
{
  "feature_branch": "<branch>",
  "parent_jira_key": "<key or null>",
  "sdd_path": "<repo-relative SDD path>",
  "approved": false,
  "approved_at": null,
  "approved_by": null,
  "tasks_created_at": "<ISO-8601 UTC, now>",
  "retro_offered": false,
  "last_memory_write": null,
  "last_test_run": null,
  "tasks": [
    {
      "id": "<TaskCreate ID>",
      "title": "<imperative title>",
      "complexity": "S|M",
      "status": "pending",
      "needs_recheck": false,
      "blocked_by": ["<task ids>"],
      "mitigates": ["<risk names>"],
      "jira_key": "<key or null>",
      "acceptance_criteria": ["<criterion>"]
    }
  ]
}
```

`approved` is always written `false`. **You never set it to true.** When the user replies "approved", the UserPromptSubmit hook flips it; the approval-gate hook denies source edits until then and denies any attempt by you to flip it.

### Step 8 — Jira comment on the parent (if enabled)
```
🔀 Breakdown created by Claude Code (takshak)

- [PROJ-77] Add DB schema migration (S)
- [PROJ-78] Implement data access layer (M) — depends on PROJ-77

SDD: <sdd path>
```

### Step 9 — Checkpoint
`phase: 3`, `phase_label: "Tasks created, awaiting approval"`, `next_step: "User reviews the plan and replies approved"`.

### Step 10 — Handoff
End with the dependency tree and:
```
Review the plan and risk coverage above, then reply "approved" to start implementation.
Commit messages should reference the task (T<id>) or Jira key so drift detection can map them.
```

## Token Budget Rules
- Read the SDD once
- task_state.json is authoritative; checkpoint.json is transient
- Output the tree and task summaries only, never the full SDD
