---
name: checkpoint
description: Show, write, or clear takshak's resume checkpoint, or clear the current plan. Use when the user asks to save progress, discard an interrupted session, or abandon the current task plan.
argument-hint: "[show | save | clear | clear-plan]"
---

# /takshak:checkpoint

Action: `$ARGUMENTS` (default `show`).

- **show** — print `.claude/checkpoint.json` as: skill, phase, task, next step, age.
- **save** — write `.claude/checkpoint.json` now: `timestamp` (ISO-8601 UTC with offset), `feature_branch`, `active_skill`, `active_task_id`, `phase`, `phase_label`, `files_modified_this_session`, `next_step` as the literal next action (file and line), `pending_decisions`, `notes`.
- **clear** — confirm, then write `{}` to `.claude/checkpoint.json`.
- **clear-plan** — for abandoning the current breakdown. Confirm with the user, naming the SDD and the number of open tasks. Then reset `.claude/task_state.json` to the empty template (`tasks: []`, `approved: false`, other fields null/false) and clear the checkpoint. The SDD and any Jira issues are left as they are — say so.
