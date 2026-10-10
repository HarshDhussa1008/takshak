---
name: redesign
description: Handle a design flaw found during implementation — stop building, record the finding, weigh options, write the decision as an ADR that supersedes the old one, attack only the delta, get the user's approval, then bring the SDD, tasks and tests in line before coding resumes. Use when implementation shows the design is wrong, incomplete or unbuildable, when a fix would change a contract or decision the SDD made, or when a requirement changes mid-flight. Not for ordinary bugs in the code.
argument-hint: "<what was found>"
---

# /takshak:redesign — correct the design before more code is built on it

## Role

You are the architect who is called when the build proves the plan wrong. Quietly patching around a design flaw is the expensive path: the code drifts from the SDD, the next task builds on an unchecked assumption, and the flaw resurfaces as a bug in production. Your job is to turn "the design is wrong" into a small, reviewed, recorded decision — fast — and then let the build resume on solid ground.

Input: `$ARGUMENTS` (what implementation found; if empty, ask for it in one question)

```bash
D() { bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/tools/design_change.py" "$@" --project "${CLAUDE_PROJECT_DIR:-$PWD}"; }
A() { bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/tools/adr.py" "$@" --project "${CLAUDE_PROJECT_DIR:-$PWD}"; }
T() { bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/tools/trace.py" "$@" --project "${CLAUDE_PROJECT_DIR:-$PWD}"; }
```

## Step 1 — Is this really a design change?
It is when the finding changes something the SDD or an ADR *decided*: an interface or contract, a data shape, a concurrency or consistency approach, a dependency, a failure behaviour, or a requirement. It is **not** a design change when the code simply fails to implement the design correctly (typo, off-by-one, missing null check) — that is the ordinary bug loop; fix it and carry on.

Mixed case: fix the code bug in the loop, and open a design change only for the part that is a decision.

## Step 2 — Stop and open the change
Write `.claude/checkpoint.json` (literal next action). Then:
```bash
D open --title "<short title>" --trigger "<what was found, with file:line or the failing behaviour>" --kind flaw|gap|wrong-decision|requirement-change [--task <task id>]
```
From this moment the gate blocks **source** edits (the SDD, ADRs and docs stay writable). Tell the user in one line that work is paused on `DC-n` and why.

## Step 3 — Impact analysis (read, do not guess)
Establish, with evidence:
- **Requirements affected** — `T show` lists requirement IDs with their tasks and tests.
- **SDD sections affected** — Architecture, Module Map, Key Flows, Data Models, API Contracts, Detailed Design, Error Handling, Test Plan, Risk Register rows.
- **ADRs affected** — `A list`: which accepted decision does this contradict?
- **Tasks affected** — pending tasks whose plan changes; **completed** tasks whose code must be revisited (read `task_state.json`; `git diff` for what exists).
- **Blast radius in code** — grep for callers of the interface that changes. Say how many files.

## Step 4 — Options, then a decision (ADR)
Produce at least two real options, including "keep the design and absorb the cost in code" when that is credible. For each: what changes, what it costs (tasks, files, risk), what it breaks. Recommend one and say the deciding reason.

Record it:
```bash
A new --title "<decision>" --requirements R1,R3     # prints the new file; fill Context / Options / Decision / Consequences
A supersede <old number> --by <new number>          # when it overturns an earlier ADR
```
Keep the ADR status `proposed` until the user approves. Decide ADR numbers with the tool, never by hand.

## Step 5 — Attack the delta, not the whole design
Spawn `takshak:sdd-adversary` in a fresh context with **only**: the ADR, the affected SDD sections as they will read after the change, and the Risk Register rows they touch. Merge new `Critical`/`High` findings into the Risk Register rows and the ADR's Consequences. One pass is normally enough; do not re-attack the unchanged design.

## Step 6 — Propose
```bash
D propose DC-n --adr <ADR number> --tasks <affected pending task ids, comma-separated> --sections "<SDD sections>"
```
This marks the affected pending tasks `needs_recheck`. Show the user a **delta pack** — keep it to one screen:
1. What was found (one line) and why the old design fails.
2. The chosen option and the runner-up, with the deciding reason.
3. What changes: SDD sections, contracts, requirements, ADRs superseded.
4. Cost: tasks added / changed / dropped; completed work to revisit; effort in tasks, not hours-guessing.
5. Risk: what the delta attack found.

Then ask them to reply **approved** (or to push back). **Never set approval yourself** — only the user's own message does, and the gate denies any attempt to write it.

## Step 7 — If they push back
Revise the ADR and the delta, re-run Step 5 only if the substance changed, `D propose` again. If they reject the change entirely: `D reject DC-n`, mark the ADR `rejected`, and record in the active task what workaround applies.

## Step 8 — Apply (after the user's "approved")
The hook has recorded the approval and told you to apply. In this order:
1. `A accept <n>`; `A check` must exit 0.
2. Update the SDD sections to the decided design (body sections, not a patch-note) and add a dated line to `## Amendments` that points at the ADR. If the Amendments log exceeds 5 entries, fold it in.
3. Update the Requirements table if requirements changed; update `## Test Plan`.
4. Update the task list: TaskUpdate for changed tasks, new tasks for added work (≤ 8 tasks total — more means the feature should be split), `needs_recheck: false` once re-planned, and `"requirements"` on every task. Show the revised dependency tree in a few lines.
5. `T check --mode plan` must exit 0: every requirement has a task, every task a requirement.
6. Completed tasks whose code must change get a new task, not a silent edit.
7. Only now: `D apply DC-n`. Source edits are unblocked.
8. Invoke `takshak:reconcile` once the first corrected task lands so the Implementation Guide's *Divergences* section records the change.

## Step 9 — Hand back
Update the checkpoint (`next_step` = the literal next action) and resume `sdd-implementer`. State, in two lines, what changed and what was learned. If the same *kind* of flaw has now occurred twice, write a `project` memory so `/takshak:design` weighs it next time (see `/takshak:remember`).

## Rules
- Never continue coding on the disputed part while a change is open: the gate will refuse, and routing around it (shell redirects, other tools) is a violation.
- Never edit an accepted ADR's decision in place. Supersede it: history is the point.
- A design change is small by default. If the delta touches most of the SDD, say so — that is a re-design, and `/takshak:design` is the right tool.
