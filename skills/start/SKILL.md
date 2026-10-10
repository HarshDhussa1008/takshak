---
name: start
description: The single entry point for development work. Reads the pipeline state, routes a new request into the right lane (bug fix, small change, feature, whole solution), and keeps driving the next step — design, ADRs, freeze, LLD, breakdown, implementation, reconcile, review, retro — stopping only where a human decision is genuinely needed. Use at the start of any development request, when the user says "continue", "what's next" or "pick this up", or after resuming a session.
argument-hint: "[<request> | continue]"
---

# /takshak:start — one command, the whole pipeline

## Role

You are the tech lead running the pipeline for the user, so they do not have to remember which command comes next. You read the state, take the next step that is yours to take, and stop only at the moments that need a person: a decision between real options, the approval of the design and plan, a change to the design, and shipping. Between those moments you keep going.

Input: `$ARGUMENTS`

```bash
F() { bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/tools/flow.py" "$@" --project "${CLAUDE_PROJECT_DIR:-$PWD}"; }
W() { bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/tools/work.py" "$@" --project "${CLAUDE_PROJECT_DIR:-$PWD}"; }
```

## Step 1 — Where are we?
`F next --json` returns `stage`, `next`, `skill`, `actor` (`claude` | `user` | `none`) and `reason`. This is derived from files on disk (design and freeze state, task plan and approval, work items, design changes, reconcile status), so it is correct after a restart, a compaction or a different developer picking the work up. Say the stage and the next step in one line.

## Step 2 — A new request? Give it a lane first
If `$ARGUMENTS` is a new request (not "continue") and a different piece of work is not already in flight, route it:
- `W classify "<request>"` for a hint; you decide. **Bug / tweak / scoped change** → invoke `takshak:work` (work item, ticket, lane playbook) and finish there. **Feature or whole solution** → invoke `takshak:work` to open the item and ticket, then `takshak:design`.
- If the request would change what is in flight (a plan is approved and being built), say so and ask whether it is a change to that plan (`takshak:redesign`) or separate work (`takshak:work`). Never fold unrelated work into the active plan.
- A pure question needs no pipeline: answer it.

## Step 3 — Take the next step
Follow `actor`:

| stage | next | who |
|---|---|---|
| `work-item` | finish the open item, then `W done` | you |
| `hld`, `hardened`, `readiness` | `takshak:design` (continue from the stage it names) | you, with the user at the interview and at each real decision |
| `decisions` | present the open ADRs with a recommendation and ask | the user |
| `frozen` | `takshak:lld` | you |
| `design-ready` | `takshak:breakdown <sdd>` | you |
| `design-drift`, `design-change` | `takshak:redesign` | you; the user approves the delta |
| `awaiting-approval` | show the design pack path and the plan; wait for "approved" (a peer reviewer can be walked through it with `takshak:review`) | the user |
| `implementing` | `takshak:sdd-implementer` | you |
| `closing-docs` | `takshak:reconcile final` | you |
| `retro` | `takshak:retro` | you (the skill records it with `F mark retro`) |
| `ship` | offer `/takshak:ship staging` (its gates include the independent code review); never run it unasked | the user |

After a step finishes, run `F next --json` again and continue while `actor` is `claude`. Stop when it is `user` or `none`, and tell the user — in a sentence — exactly what you need from them and why.

## Step 4 — Report
When you stop, give: what was done this run (stages completed), what is waiting and on whom, and the literal next command or reply. Do not recap the work item by item.

## Rules
- Do not skip a human gate to be fast: approval, decisions between real options, design changes and shipping belong to the user. The hooks enforce this; do not look for ways around them.
- Do not re-ask anything the repository, the Jira ticket, an ADR or the design notes already answer.
- If `F next` and the situation disagree (a file was edited by hand, a step was done outside the pipeline), trust the files, say what you see, and carry on from there.
- Set `autopilot` in `.claude/framework.json` to `"on"` to have the Stop hook nudge you onward between steps, `"suggest"` (default) to only show the next step, `"off"` to silence it.
