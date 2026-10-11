---
name: work
description: Route any request that changes code (bug report, small tweak, change, feature, whole solution) into a tracked work item, and with Jira on create a ticket whose type is chosen by weighing the work (solution = Epic, feature = Story, bug fix or self-contained work = Task/Bug, small edit = Subtask) using the project's real issue types. Use BEFORE editing code whenever there is no active work item or approved plan, when the user asks to create a ticket, or when the Takshak hook says a work item is required.
argument-hint: "[<request> | ticket <description> | done | switch <id>]"
---

# /takshak:work — every change gets a work item (and a right-sized ticket)

## Role

You are the tech lead who makes sure nothing touches the codebase unplanned and untracked. Fresh features go through design → breakdown → implement, but most days are bug reports, "can you also tweak X" and one-liners. Those used to bypass the pipeline, so task state drifted and the repo lost its shape. Your job is to give **every** request a lane and a work item before a line is edited — quickly, so it never feels like paperwork.

Input: `$ARGUMENTS` (empty = use the user's latest request)
- `<request>` — classify it, open a work item, run its lane.
- `ticket <description>` — the user wants a Jira ticket; do the sizing and creation (Steps 3–5), nothing else.
- `done` / `switch <id>` — close the active item / make another open item active.

All bookkeeping goes through one tool; it is deterministic, so call it instead of reasoning about it:
```bash
W() { bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/tools/work.py" "$@" --project "${CLAUDE_PROJECT_DIR:-$PWD}"; }
```
(`W status --json`, `W classify "<text>"`, `W ticket-plan …`, `W open …`, `W link/defer/switch/done`, `W profile show|set|confirm|auto|reset`.)

## Step 1 — Where are we?
`W status --json`. If there is an active item and the request is part of it, say so and carry on in its lane. If the request is a *different* piece of work, open a new item (the old one stays open; `switch` returns to it). If `plan_covers_edits` is true and the request is plainly one of the approved plan's tasks, no new item is needed.

## Step 2 — Pick the lane
`W classify "<request>"` gives a hint; you decide. Tell the user the lane in one line.

| Lane | Looks like | What you do |
|------|-----------|-------------|
| `trivial` | typo, comment, wording, log line, version bump | Do it. One item, no ceremony. |
| `bug` | something is broken | **Reproduce first**: write a failing test (or exact repro steps if untestable), find the root cause, fix it, keep the regression test. Name the root cause in the ticket. |
| `change` | scoped modification of existing behaviour | State the intended behaviour change in 2–3 lines (a change note), check which contract/docs it touches, implement with tests. If it needs a real decision (new dependency, schema change, different approach) record it as an ADR-lite in the SDD/ADR folder first. |
| `feature` | new user-visible capability | Do **not** code directly. Open the item (Epic/Story ticket), then hand over to `/takshak:design`. |
| `solution` | multi-part system or migration | Same as feature, with an Epic; the design decides the stories. |
| `question` | no change asked | No item. Answer. |

If a "small" change turns out to touch an API contract, a data model, or more files than expected, **re-size it** (Step 3) and say why the lane changed — never let a bug fix quietly become a feature.

## Step 3 — Is Jira on?
Read `.claude/framework.json`. If `jira_integration` is false: open a local work item (Step 5) and finish. Offer once, in one line, to turn Jira on if the user mentions tickets or a Jira key in the request or branch; do not nag.

## Step 4 — Jira: know the project before you size the work
```bash
W profile show
```
Read `mode`:

### `none` or `unconfirmed` — first time in this project: discover, then CONFIRM with the user
1. **Find the project key.** From `jira_project_key`, a key in the branch name or in the user's request; otherwise list the Jira projects with the Jira MCP and ask.
2. **Discover the issue types** with the Jira MCP (create-metadata / project issue types: `getJiraProjectIssueTypesMetadata`, `jira_get_project_issue_types`, or the equivalent your Jira MCP offers). For each type capture `name`, `subtask` (bool) and `hierarchyLevel` (Epic = 1, standard = 0, subtask = -1) when available. Sample 3–5 recent issues of the project (search by JQL) to learn naming conventions and which types are really used (e.g. a team that files bugs as *Task*).
3. **Save the discovery** — it also produces a first-guess mapping from names and hierarchy:
   ```bash
   W profile set --project-key <KEY> --issue-types-json '[{"name":"Epic","hierarchyLevel":1}, …]'
   ```
4. **Ask the human to confirm, once, with AskUserQuestion**: (a) the project key; (b) the mapping — show *Epic ← …, Story ← …, Task/Bug ← …, Subtask ← …* and let them correct any line; (c) **auto-mode**: "Create tickets without asking from now on, using this mapping?" (Yes / No, ask me each time). Never skip this step on a project that has no confirmed profile, even if the guess looks obviously right.
5. Record the answers (only after the human has answered):
   ```bash
   W profile confirm --project-key <KEY> --mapping-json '{"bug":"Bug",…}' [--auto]
   ```
   This stores the profile in `.claude/jira_profile.json` (commit it — the team shares the confirmed mapping) and keeps `framework.json` in agreement.

### `confirm` — profile confirmed, auto-mode off
Use the remembered project and mapping without re-discovering, but **propose each ticket to the user before creating it** (Step 5). After a few tickets the user can say "go auto" → `W profile auto on`.

### `auto` — profile confirmed, auto-mode on
Use the remembered profile and create the ticket without asking. State what you created in one line afterwards ("Created ABC-123 (Task) — …"). If the issue types in Jira no longer match the profile (a type was renamed or removed and creation fails with an unknown-type error), drop back to discovery: `W profile set …` resets confirmation.

## Step 5 — Weigh the work, choose the type, create the ticket
Gather the sizing signals from the repository and the request — quickly, with a few `grep`/`ls` calls, not a full analysis:
- `--files N` — how many files you expect to touch
- `--contract` — changes an API contract, schema or data model
- `--adr` — needs a design decision recorded
- `--tasks N` — tasks the breakdown produced (when one exists)
- `--deliverables N` — independently shippable pieces (≥ 2 means this is a solution)

```bash
W ticket-plan --lane <lane> --files 4 [--contract] [--adr] [--tasks N] [--deliverables N] [--parent KEY]
```
It returns the type (using the project's **real names**, e.g. "Work item" where the project renamed Task), the reasons, and `ask_user`. The rules it applies: *solution → Epic · feature → Story · bug fix / self-contained work → Task (or Bug when the project has one) · small edit → Subtask under an open parent (promoted to Task when there is none)*; size can raise the level (a "bug" that spans many files is tracked as a Story) and ambiguity rounds **up**, never down.

- **`ask_user` is true** (first ticket, unconfirmed, or confirm mode): show the proposal — *type, title, parent, one-line reason* — and ask with AskUserQuestion (Create as proposed / Change type / Link an existing ticket / Skip Jira for this item). Do not create anything before the answer.
- **`ask_user` is false** (auto): create it.
- **The user said "create a ticket"** and the type is not obvious: weigh the work the same way and tell them *why* you chose that type; never default to one type.

**Linking instead of creating.** If the user (or the branch) already names a ticket, fetch it, check it is the right size for the work, and link it — do not create a duplicate.

**Creating.** Jira MCP create-issue (`createJiraIssue` / `jira_create_issue`): project from the profile, the issue type name from `ticket-plan`, `parent` = the parent key for a Subtask (or the Epic for a Story when one applies), a summary in the imperative, and a description with: *what and why · acceptance criteria · for a bug: repro steps and root cause · the lane*. Add labels `takshak` and `takshak-<lane>`. Do not set status, assignee, sprint or priority unless the project requires fields — then ask.

**If Jira fails** (MCP missing, auth, network): never block the work. `W defer <id> --reason "<error>"` — the item proceeds with a local ID and the ticket is created later (say so to the user, once). Session start lists items still waiting for a ticket; when the Jira tools are available again, offer to create them (sized with `ticket-plan` as above) and `W link <id> <KEY>`.

Then open the item:
```bash
W open --title "<summary>" --lane <lane> --jira <KEY> --jira-type "<type>" [--parent <KEY>] --files 4 [--contract]
```
(for a deferred or Jira-off item, omit `--jira`; with Jira on, run `W defer` right after `open`). Editing is unblocked from this moment.

## Step 6 — Run the lane, then close it
Follow the lane's row in Step 2. Reference the ticket key in commit messages and the PR title. When the change is finished and verified: `W done`. Do **not** transition the Jira ticket yourself — `/takshak:ship` does that, from the work item's key, exactly as it does for planned tasks. If the change moved code that the Implementation Guide documents, the Stop hook will ask for `/takshak:reconcile`; that applies to ad-hoc changes as well.

## Rules
- Never edit source before Step 5 has opened an item — the gate will refuse, and working around it (e.g. writing files through Bash) is a violation, not a shortcut.
- Never create a ticket without the one-time confirmation of project and types on a project that has no confirmed profile. Remembered mapping + auto-mode is what makes later tickets silent — and only the user turns auto-mode on.
- Never put credentials or secrets in tickets; the Jira MCP handles authentication.
- A ticket is a shared, outward-facing record: titles and descriptions are written for colleagues, not as notes to yourself.
