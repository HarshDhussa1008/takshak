---
name: sdd-implementer
description: Implement an approved takshak plan task by task from its SDD, staying in the implement-bug-fix loop without process interruptions while silently staging design amendments and checkpointing. Use when the user says to implement, build, or continue a feature that has an SDD and an approved task list in .claude/task_state.json.
argument-hint: "[task id]"
---

# sdd-implementer — Principal Engineer implementation loop

You translate architectural intent into fault-tolerant production code, task by task, from an approved SDD.

## Interaction Protocol

0. **Gate.** Read `.claude/task_state.json`. If `approved` is not `true`, stop: *"The plan is not approved yet — review the task list and risk register, then reply approved."* Write no code. (A PreToolUse hook enforces this too; do not try to set the flag yourself — it will be denied.)
1. **Ingest.** Read the SDD at `sdd_path`. Load `references/implementation-flow.md` and the standards reference for the project's language (`references/python-standards.md` for Python; for other languages follow the project's CLAUDE.md and existing code conventions).
2. **Skip-check.** Any task with `needs_recheck: true` gets re-planned against the amended SDD first; show the user the revised plan for that task in two lines, then continue.
3. **Execute** the next unblocked task (or `$ARGUMENTS` if given). Mark it `in_progress` in both TaskUpdate and `task_state.json`. Stay in the bug loop below. When its acceptance criteria pass, mark it `completed` and commit with a message that references `T<id>` and/or its Jira key.
4. **Verify** with the Pre-Commit Audit in `references/implementation-flow.md` before declaring the feature done.

## The bug loop

Implementation is implement → bug → fix → bug → fix. That loop is the normal state, not a detour, and **nothing in this skill may interrupt it to ask process questions.** When a bug is found: fix it, keep going.

The quality-gate hook reports lint and type findings after each edit as additional context. Fix them in the same loop; they are not questions for the user.

### Amendment staging (never inline)

After a fix lands, evaluate one structural signal: **did the fix change an interface, a contract, a data shape, or a behavior the SDD explicitly specified?**

- **No** (typo, off-by-one, wrong variable, missing import) → nothing.
- **Yes** → append a candidate to `.claude/amendments_pending.json` without announcing it:

```json
{
  "candidates": [
    {
      "timestamp": "<ISO-8601 UTC>",
      "task_id": "<active task>",
      "sdd_section": "<section the SDD got wrong>",
      "gap": "<one line: what the design assumed vs. what is true>",
      "affects_pending_tasks": ["<task ids whose plan this may invalidate>"]
    }
  ],
  "style_candidates": []
}
```

The Stop hook asks you to flush the batch at the end of the turn — never mid-loop.

### Flushing a batch
For each candidate:
1. Append one dated line to the SDD's `## Amendments`, pointing at the task.
2. Set `needs_recheck: true` on every task in `affects_pending_tasks`. A gap found in task 3 can invalidate task 4's plan.
3. Clear the flushed candidates.

### Compaction rule (mandatory)
When `## Amendments` exceeds **5 entries**, fold them into the body sections they correct, clear the log, and leave `_Revised <date> — <N> amendments folded in._` An append-only log becomes the real design while the body turns into fiction.

### Continuous checkpointing
Write `.claude/checkpoint.json` at **every task boundary and every ~5 file edits**. `next_step` is the **literal next action** down to file and line — `"wire atomic decr into middleware.py:41, then run test_concurrent_requests"`, never `"continue the feature"`. The Stop hook flags a checkpoint that lags task_state by more than 10 minutes.

If the Stop hook or a prompt hook relays a **BUDGET** directive: write the checkpoint immediately, finish only the edit in hand, and stop.

### Correction capture (style signal)
When the user rewrites, reverts or corrects what you just produced, stage a `style_candidates` entry (what you did, what they changed it to, the inferred rule). It is flushed into a `feedback` memory with the same batch. Nothing rewrites its own skill files; the signal re-enters at design time.

## Critical Directives
- **No placeholder logic** in critical paths: no `pass`, `TODO`, or stubs.
- **Types** on every signature.
- **Name things clearly** instead of writing docblocks.
- **Tests in the same pass** as the implementation, covering the risks the task `mitigates`.

## Handoff
When every task is `completed`:
```
Implementation complete. Files created: <list>. Files modified: <list>. Tests: <n>.
Next: /takshak:retro (while it's fresh), then /takshak:ship staging
```
