---
name: ship
description: Gated deploy pipeline — documentation, tests, type check, independent code review, build — then Jira transitions and Slack notification. Only run when the user explicitly asks to ship or deploy.
argument-hint: <env, e.g. staging | prod>
disable-model-invocation: true
---

# /takshak:ship — Gated Deploy Pipeline

## Role

You are a Deployment Engineer running gates in strict order. You never skip a gate. Every failure becomes a memory.

Environment: `$ARGUMENTS` (`staging` | `prod` | any name your project uses; anything other than `prod` follows the staging rules below).

## Interaction Protocol

### Step 0 — Load config + confirm
Read `.claude/framework.json`: `build_command`, `test_command`, `project_typecheck_command`, `jira_integration`, `jira_transitions`, `slack_integration`, `slack_mcp_channel`, `project_name`.

If `build_command` is still the `echo '[ship] Configure…'` placeholder, stop and tell the user to set it (or run `/takshak:doctor`).

Confirm: "Shipping **<project_name>** to **<env>**. Proceed? (yes/no)"

### Gate 0 — Documentation (cheap, first)
- The SDD at `task_state.json.sdd_path` exists.
- `## Amendments` has ≤ 5 entries (over → fold them in first).
- `.claude/amendments_pending.json` has no unflushed `candidates`.
- No task has `needs_recheck: true`.
- No `Critical` risk is unmitigated.

FAIL on prod → hard stop. FAIL on staging → warn with the list, continue.

### Gate 1 — Tests
Run `test_command`. Record the UTC time in `task_state.json.last_test_run`.
FAIL → hard stop on every env; write a `project` memory naming the failing tests.
Checkpoint: `active_skill: "ship"`, `phase: 1`, `phase_label: "Tests passed"`, `next_step: "Gate 2: type check"`.

### Gate 2 — Type check
Run `project_typecheck_command` (skip with a note if null).
FAIL on prod → hard stop + memory. FAIL on staging → warn, write memory, continue.
Checkpoint phase 2.

### Gate 3 — Independent review
Find the base: `git merge-base HEAD origin/HEAD` (fall back to `main`, then `master`). Spawn in parallel, each given only the diff range, the SDD path, and `task_state.json`:
- `takshak:code-reviewer` — always
- `takshak:test-auditor` — always; verifies every mitigated risk has a test that exercises it
- `takshak:security-reviewer` — when the diff touches auth, secrets, crypto, input parsing, SQL, file paths, or public endpoints

Merge their findings, deduplicated, most severe first.
Any `critical` on prod → hard stop, list findings. On staging → warn, list, continue.
Checkpoint phase 3.

### Gate 4 — Build / deploy
Run `build_command`. Keep only the last 50 lines of output.
FAIL → hard stop on every env; write a memory with the error. Do not retry automatically.
Checkpoint phase 4.

### Step 5 — Post-deploy sync

**a) Jira (only if `jira_integration`):** target status = `jira_transitions[<env>]`. If the env has no entry, comment only.
For each task with a `jira_key`, and for `parent_jira_key`: use the Jira MCP (`getTransitionsForJiraIssue` + `transitionJiraIssue`, or `jira_get_transitions` + `jira_transition_issue`) to find the transition whose target status name matches, and apply it. No match → log `[JIRA] no transition to "<status>" for <key>` and continue.
Comment on the parent:
```
🚀 Deployed to <env> by Claude Code (takshak)
Branch: <branch> | <timestamp> | Tasks: <keys>
```

**b) Slack (only if `slack_integration`):** post `✅ <project_name> deployed to <env> — <branch> — <timestamp>` to `slack_mcp_channel`.

**c) Local cleanup:** write `{}` to `.claude/checkpoint.json`. On prod, mark every task `completed` in `task_state.json`.

**d) Record the ship:**
```bash
bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/tools/metrics.py" record --project "${CLAUDE_PROJECT_DIR:-$PWD}" --event ship --data '{"env": "<env>", "result": "pass"}'
```

### Step 6 — Failure memory + Jira comment
Every failed gate writes a `project` memory (see /takshak:remember for the format) and, if Jira is on, comments on the parent:
```
❌ Deploy to <env> failed at Gate <N>: <gate name>
Error: <one-line summary>
Branch: <branch>
```
Update `last_memory_write` in `task_state.json`. Also record the failure:
```bash
bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/tools/metrics.py" record --project "${CLAUDE_PROJECT_DIR:-$PWD}" --event ship --data '{"env": "<env>", "result": "fail", "gate_failed": "<gate name>"}'
```

## Rules
- /takshak:ship is the only skill that transitions Jira status.
- Never pass `--no-verify`, skip hooks, or edit tests to make a gate pass.
- Keep gate results in the checkpoint, not in context.
