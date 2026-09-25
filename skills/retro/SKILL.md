---
name: retro
description: Structured feature retrospective once implementation finishes — diffs plan vs. commits, asks four questions, writes durable memories, appends Lessons Learned to CLAUDE.md, flags stale memories, and comments (never transitions) on Jira. Use when the user wants a retro or when all takshak tasks are complete.
---

# /takshak:retro — Structured Feature Retrospective

## Role

You facilitate a retrospective as soon as implementation finishes — before /takshak:ship, not after. You turn the answers into durable memories and CLAUDE.md lessons, and flag stale memories.

**Never transition Jira status here.** /takshak:ship owns the Jira lifecycle, gated by a real deploy. /takshak:retro only comments.

## Interaction Protocol

### Step 1 — Context (in parallel)
- `.claude/framework.json`: `jira_integration`, `project_name`, `memory_path`
- `git log <default-branch>...HEAD --oneline` — what was actually built
- `.claude/task_state.json` — the plan, Jira keys
- If Jira is on: search `issue in (<jira keys>)` (`searchJiraIssuesUsingJql` / `jira_search`) for `key`, `status`, `resolution`

Diff plan vs. commits: unimplemented tasks, unplanned commits, amendments count from the SDD.

Open with a 3-line summary of that diff — it is the evidence the questions are about.

### Step 2 — Four questions, one at a time
1. "What was harder than estimated, and why?" → `feedback` memory on estimation
2. "Anything you'd design differently now (architecture, data model, API shape)?" → `project` memory on design regrets
3. "Any pattern that should never be repeated?" → `feedback` anti-pattern memory
4. "Any pattern that worked well and should be repeated?" → positive `feedback` memory

### Step 3 — Write memories
Write one memory per **actionable** answer — skip answers with nothing reusable rather than padding. Use the /takshak:remember format (frontmatter `name`, `description`, `type`; body with **Why:** and **How to apply:**), file name `retro_<branch-slug>_<slug>.md`, and add each to `MEMORY.md`.

Update `last_memory_write` in `task_state.json`.

### Step 4 — Update CLAUDE.md
Append under `## Lessons Learned`:
```markdown
### <branch> (<date>)
- <one line per anti-pattern or key decision>
```
If the section exceeds 20 entries, remove the oldest 5.

### Step 5 — Stale memories
List memory files not modified in 90+ days: `[STALE] <file> — last modified <date>`, and ask once for the batch: archive, update, or keep.

### Step 6 — Jira comment (if enabled; never a transition)
On `parent_jira_key`:
```
🔄 Retro complete — built, not yet shipped
Branch: <branch> | Memories written: <n>
Jira status changes on /takshak:ship.
```

### Step 7 — Summary
```
Retro complete. <N> memories written. CLAUDE.md updated. <M> stale memories flagged.
Next: /takshak:ship <env> when ready.
```

## Rules
- Never skip the four questions.
- Write what worked, not only corrections.
- Do not duplicate what CLAUDE.md's "Do Not Do" already says.
