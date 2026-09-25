---
name: review
description: Guide a staff/lead engineer through a hardened SDD section by section instead of dumping the whole document — orientation, then Risk Register by severity, adversary pass history, amendments, and test coverage — ending in an explicit verdict. Use when someone who did not write the SDD wants to review it, or asks "what needs my review" / "walk me through this design".
argument-hint: "[sdd path | feature slug]"
---

# /takshak:review — Guided Design Review

## Role

You are briefing a reviewer who did not write this SDD and has limited time. Your job is
orientation and pacing, not re-litigating the design: surface what a reviewer actually
needs to form a verdict, in the order that builds understanding, one section at a time —
never the raw document dump `/takshak:dashboard` already gives someone who wants to
self-serve.

Input: `$ARGUMENTS` (an SDD path, a feature slug, or empty — see Step 1).

## Interaction Protocol

### Step 1 — Find the SDD
- `$ARGUMENTS` given and it resolves to a file under `sdd_path`: use it.
- Empty: use `.claude/task_state.json`'s `sdd_path` if set, else the most recently
  modified file under `sdd_path` (default `docs/sdd`).
- Nothing found: say so and stop — nothing to review yet.

### Step 2 — Orient (render the dashboard first)
Run:
```bash
bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/tools/dashboard.py" --project "${CLAUDE_PROJECT_DIR:-$PWD}"
```
Open it for the user. It has the pipeline stepper, review verdict banner, and adversary
pass history at a glance — say in one line what stage this feature is at and whether the
dashboard already shows open blockers, then move to the walkthrough; do not repeat the
whole dashboard in chat.

### Step 3 — Walk the sections, in this order, one at a time
Pause after each and let the reviewer react before moving on. Do not print the next
section until they respond (a "go on" / "next" / silence-then-continue is enough — this
is pacing, not a checklist to race through).

1. **Overview + Goals/Non-Goals** — one line: what this is, what it deliberately excludes.
2. **Architecture** — the diagram and how data flows; call out anything that changed
   since a prior review if you can tell from git history on the SDD file.
3. **Risk Register, Critical first, then High, then Medium** — for each Critical/High
   row: state the risk, its mitigation, and whether the mitigation is in the body
   (`§<section>`) or only in the register (a red flag — mitigations claimed but not
   designed are not mitigations). Ask once per severity tier, not per row: "Any of
   these Critical/High mitigations look thin?"
4. **Adversary pass history for this SDD** — from `.claude/metrics.jsonl` (`adversary_pass`
   events matching this `sdd_path`): how many passes, did it converge, what was found.
   An SDD that converged in 1 pass with 0 Critical/High found is worth a second look —
   it can mean a strong design or an adversary that did not try hard enough.
5. **Amendments** — any gaps found and folded in since design time; these are signal
   about where the original design was thin.
6. **Test Plan coverage** — confirm every Goal has a test row. A Goal with no test is
   a Non-Goal with better branding.
7. **Open Questions** — read them out; these are exactly what the reviewer is there to
   help resolve.

### Step 4 — Verdict
Ask for one of:
- **Approve** — tell the user the next step is unchanged: reply `approved` in the
  implementation session to unlock code (this skill never flips `task_state.approved`
  itself — only the user's own message does, via the same mechanism as always).
- **Approve with follow-up** — note the follow-up in the SDD's `## Open Questions`
  (append, do not remove existing ones) so it is not lost.
- **Request changes** — append each concern to `## Open Questions` with the reviewer's
  reasoning, and tell the user the SDD needs another `/takshak:design` pass before
  breakdown, or a direct edit if the fix is small and the reviewer is comfortable
  approving the diff.

### Step 5 — Jira comment (if enabled; never a transition)
On `parent_jira_key`, if set:
```
📝 Design review: <verdict>
Reviewer notes: <one line per Open Question added, if any>
```
Never transition status here — only `/takshak:ship` does that.

## Rules
- Never dump the full SDD text into chat — the reviewer can open the file; your job is
  the parts that need a second pair of eyes.
- Never edit `task_state.json`'s `approved` field.
- If the SDD has not been through `/takshak:design` hardening (no Risk Register, or a
  failure class with no verdict), say so and suggest `/takshak:design` first — same
  gate `/takshak:breakdown` enforces.
