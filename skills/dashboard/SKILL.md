---
name: dashboard
description: Open the read-only takshak pipeline dashboard (risks, tasks, amendments, budget, patterns, inbox) and call out anything that needs action. Use when the user asks for status, the dashboard, or an overview of the pipeline.
argument-hint: "[serve]"
---

# /takshak:dashboard — Read-only pipeline view

A visual pass for what a terminal is bad at: scanning a risk register, spotting drifted tasks, reading amendments in context, checking budget headroom. It never edits code and never replaces the CLI loop.

## Step 1 — Render
One-shot:
```bash
bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/tools/dashboard.py" --project "${CLAUDE_PROJECT_DIR:-$PWD}"
```
It writes `.claude/dashboard.html` and prints the path. Open it for the user (`start ""` on Windows, `open` on macOS, `xdg-open` on Linux).

If `$ARGUMENTS` is `serve`, run it in the background with `--serve --port 7399` and give the user `http://127.0.0.1:7399/`. It binds to 127.0.0.1 only and re-reads state on every refresh.

## Step 2 — Read the panels back
Don't just hand over a link. Call out anything that needs action:
- unmitigated `Critical` / `High` risks
- tasks flagged `needs recheck`
- staged amendments not yet folded into the SDD
- an amendment log past the 5-entry compaction threshold
- budget at or past the alert threshold
- `awaiting approval` while implementation is supposedly underway

## Step 3 — Inbox
`.claude/inbox/` is a file-drop bridge: screenshots, logs and exports placed there are listed with repo-relative paths ready to reference. If the user mentions a file they want you to look at, check the inbox first.

## Rules
- Read-only. Changes go through the normal loop.
- The dashboard is a single stdlib script with no dependencies; keep it that way.
- Never bind it to 0.0.0.0.
