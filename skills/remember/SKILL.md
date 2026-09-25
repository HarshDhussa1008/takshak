---
name: remember
description: Save a durable project learning — a decision, a correction, an anti-pattern, or a convention — as a memory that takshak re-injects at session start and design time. Use when the user says remember this, or when a pattern alert or staged style correction needs to be written down.
argument-hint: "[what to remember]"
---

# /takshak:remember — write a project memory

## Where
`memory_path` from `.claude/framework.json` if set, otherwise `~/.claude/projects/<project-folder>/memory/`, where `<project-folder>` is the absolute project path with every non-alphanumeric character replaced by `-` (`D:\app` → `D--app`, `/home/u/app` → `-home-u-app`).

## What
From `$ARGUMENTS`, or from the conversation if empty (the most recent correction, pattern alert, or decision). Classify:
- `feedback` — how to work here: style rules, corrections, anti-patterns, things that worked
- `project` — facts about this system: design decisions and their reasons
- `user` — the user's own preferences
- `reference` — pointers to docs, dashboards, runbooks

If a memory on the same subject exists, update it instead of adding a near-duplicate.

## Format
`<type>_<short-slug>.md`:
```markdown
---
name: <short-slug>
description: <one line that will match future work — name the domain words>
type: <feedback|project|user|reference>
---
<the rule or fact, one or two lines>

**Why:** <the reason or incident behind it>
**How to apply:** <when it applies and what to do>
```
Add `- [<name>](<file>) — <description>` to `MEMORY.md` in the same directory.

## Bookkeeping
- If this came from a pattern alert, set `memory_written: true` for that code in `.claude/anti_pattern_registry.json`.
- If it flushed `style_candidates`, clear them from `.claude/amendments_pending.json`.
- Set `last_memory_write` in `.claude/task_state.json` to now (ISO-8601 UTC).

Confirm in one line: which file, which type.
