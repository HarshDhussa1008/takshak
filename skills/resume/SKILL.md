---
name: resume
description: Continue interrupted takshak work from .claude/checkpoint.json — reports where it stopped, checks the budget window has reset, and picks up the literal next action. Use when the user says resume, continue, or pick up where we left off.
---

# /takshak:resume — continue from the checkpoint

1. Read `.claude/checkpoint.json`. If it is empty or has no `active_skill`, say there is nothing to resume and summarise `task_state.json` instead (done / open / needs_recheck).
2. Read `.claude/budget.json`. If `alert.tripped` and `resets_at` is still in the future, tell the user the window resets at `resets_at_iso` and recommend waiting; continue only if they say so.
3. Show, in at most four lines: the skill and phase, the active task, files touched, and `next_step`.
4. Check the working tree matches the checkpoint (`git status --porcelain`, and that files in `files_modified_this_session` exist). If something diverged, say what, and re-derive the next step from `task_state.json` before continuing.
5. Continue by re-entering the skill named in `active_skill` (`design`, `breakdown`, `sdd-implementer`, `ship`) at the recorded phase, starting with `next_step`. Do not re-run completed phases.
