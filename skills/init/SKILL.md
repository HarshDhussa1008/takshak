---
name: init
description: Set up takshak in the current project in one step — detects the stack, writes .claude/framework.json and state files, wires the budget statusline, seeds CLAUDE.md, optionally commits team settings so every teammate gets the plugin, and migrates legacy copy-installed hooks. Use when the user wants to install, set up, onboard, or migrate takshak.
argument-hint: "[--team] [--migrate]"
---

# /takshak:init — one-step project setup

## Step 1 — Run the bootstrap
```bash
bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/tools/bootstrap.py" init \
  --project "${CLAUDE_PROJECT_DIR:-$PWD}" --plugin-data "${CLAUDE_PLUGIN_DATA}" $ARGUMENTS
```
It is idempotent and never overwrites existing config or state. Relay its report to the user as a short checklist.

- If it reports **legacy copy-installed hooks**, ask the user whether to migrate, then re-run with `--migrate` (removes the old `.claude/hooks/*.py` wiring and files so hooks don't fire twice).
- If the user wants **everyone on the repo** to get takshak automatically, re-run with `--team`. It adds the marketplace and `enabledPlugins` to `.claude/settings.json`; tell them to commit that file — teammates are prompted to install when they trust the folder.

## Step 2 — Fill the gaps that can't be detected
Read the new `.claude/framework.json` and ask, in one message, only about what is still missing:
- `build_command` if it is still the `echo` placeholder
- Jira: enable it? project key? Are the transition target statuses for staging/prod really "In Review"/"Done" in their workflow?
- Slack channel for deploy notices (optional)

Edit `framework.json` with their answers.

## Step 3 — CLAUDE.md
If CLAUDE.md was just created from the template, offer to fill its placeholders by reading the repo (entry points, stack, test layout) — show the diff before writing.

## Step 4 — Verify and hand off
Run `/takshak:doctor`'s check (the same bootstrap with `doctor`) and relay only the failures. Then end with:
```
Takshak is ready. The statusline starts reporting budget on the next session.
Next: /takshak:design "<what you want to build>"   (or a Jira key)
```
