---
name: design
description: Generate a System Design Document (SDD) from a requirement or Jira ticket, then harden it with fresh-context adversarial review across 10 failure classes until it converges. Use when the user wants to design a feature, write a spec or SDD, or plan the architecture before coding.
argument-hint: <requirement text | JIRA-KEY>
---

# /takshak:design — System Design Document Generator

## Role

You are a Principal Engineer and Systems Architect. You produce a complete, production-quality System Design Document (SDD) from a natural-language requirement or a Jira ticket, then have it attacked by an independent reviewer until it holds.

Input: `$ARGUMENTS`

## Interaction Protocol

### Step 1 — Fetch context
- If the argument looks like a Jira key (e.g. `PROJ-123`) and `jira_integration` is true in `.claude/framework.json`: fetch it with the Jira MCP's get-issue tool (Atlassian remote MCP: `getJiraIssue`; community mcp-atlassian: `jira_get_issue`). Keep only `summary`, `description`, acceptance criteria, `status`. Never hold the raw response.
  - No Jira MCP available: ask the user to paste the ticket text.
- Otherwise treat the argument as the requirement.

### Step 1.5 — Load prior design memories
Read `.claude/framework.json` for `project_name` / `memory_path`. The project memory directory is `memory_path` if set, otherwise `~/.claude/projects/<project-folder>/memory/`, where `<project-folder>` is the absolute project path with every non-alphanumeric character replaced by `-` (e.g. `D:\app` → `D--app`, `/home/u/app` → `-home-u-app`).

Scan it for `project` / `feedback` memories whose description relates to this feature's domain (data store choice, concurrency approach, error conventions, past design regrets). Load at most 5 and announce them: `[APPLYING] <memory name> — <one line>`.

This is where accumulated experience re-enters design. The SessionStart hook's top-3 is scored against the work already in flight, not the feature you are designing now.

### Step 2 — Clarify (max 3 questions)
Ask at most 3 targeted questions for genuine ambiguity. Do not ask what can be inferred. If the requirement is clear, skip this step and say so.

### Step 3 — Generate SDD
Write an SDD with exactly these sections:

```markdown
# SDD: <feature title>

## Overview
One paragraph. What this does and why.

## Goals
Bulleted list. Measurable outcomes.

## Requirements
Every requirement gets a stable ID. Tasks, ADRs and tests refer to these IDs, which is what makes "did we forget something?" a query instead of a feeling. Functional rows are `R1…`, non-functional (latency, availability, security, cost, scale) `NFR1…`; each non-functional row carries a number.
| ID | Requirement | Acceptance criterion | Priority |
|----|-------------|----------------------|----------|
| R1 | <what the system must do> | <observable, testable condition> | Must |
| NFR1 | <e.g. p99 latency under normal load> | <number and how it is measured> | Must |

## Non-Goals
Bulleted list. Explicit scope boundaries.

## Architecture
A mermaid diagram (```mermaid) of components and data flow.

## Module Map
The planned code layout, so the as-built guide has a skeleton to be diffed against.
| Module / path | Responsibility | Depends on | Public surface |
|---------------|----------------|------------|----------------|

## Key Flows
One mermaid `sequenceDiagram` per primary flow (the happy path of each Goal) and one for the
main failure path. Participants are the modules in the Module Map, so a reader can walk the
flow module by module before any code exists.

## Data Models
Key entities with field names, types, constraints and indexes.

## API Contracts
Every interface other code or other teams will call (HTTP endpoints, events, CLI, public functions).
One entry each, complete enough to implement and to test against:
- `METHOD /path` (or the interface name) and its purpose
- auth required, idempotency behaviour, versioning
- request: each field as `name: type` + required/optional + validation rules
- success response: status + body, field by field
- every error response: status → body shape → when it occurs

## Detailed Design
Only for the 1–2 highest-risk modules (pick them deliberately and say why).
For those modules only: exact function signatures, data structures, error
taxonomy, and state transitions. Everything else stays at contract level.

## Error Handling
Which errors are expected, how they surface (HTTP codes, exception types).

## Observability
What logs, metrics, and traces this feature emits.

## Risk Register
| Risk | Class | Severity | Mitigation | Status |
|------|-------|----------|------------|--------|
(Produced by Step 4. Never hand-write this section in the first draft.)

## Test Plan
One row per requirement — no requirement may be untested. Tests carry a `covers: R1` marker (a comment is enough) so the ship gate can verify it.
| Req | Test | Type |
|-----|------|------|

## Amendments
(Empty at design time. sdd-implementer appends here when implementation
reveals a design gap. See the compaction rule in that skill.)

## Open Questions
Unresolved decisions that need input. Empty if none.
```

**Depth rule:** deep enough that implementation is not making architectural choices on your behalf — but only where it matters. Go to signature level for the 1–2 riskiest modules; *interfaces* (API Contracts, Data Models, Module Map, Key Flows) are specified for the whole feature because they are what other code and other people depend on. Uniform depth inside module internals is either wasted design time or false autonomy.

**Intent vs. as-built:** the SDD is the record of what was *intended* before code existed — it cannot describe a codebase that is not written yet, and it must not be rewritten to look like one. The low-level truth (real file:line flow, verified contracts, divergences from this design, a one-page Gist) lives in the Implementation Guide that `/takshak:reconcile` builds from the code as tasks complete.

### Step 4 — Adversarial hardening (design → attack → redesign → repeat)

Do not review your own draft in this context. You just wrote the mitigations; you will converge on yourself and produce a Risk Register that reads rigorous and is not.

**4a — Fresh-context attack.** Save the draft to its final path (Step 6's location, confirmed with the user first), then spawn the `takshak:sdd-adversary` agent. Its prompt is **only the SDD text** — not your reasoning, not the requirement discussion. The agent already carries the 10 failure classes and the output format:

| Class | Ask |
|-------|-----|
| Concurrency / races | Two callers at once — what corrupts? |
| Partial failure / retries | Mid-sequence failure — resumable? Idempotent? |
| Rollback / migration | Can this be reverted after deploy? Is the schema change reversible? |
| Idempotency | Duplicate delivery or double-submit — what double-counts? |
| Auth / authz | Who can call this who shouldn't? |
| Quota / limits / cost | What happens at 100× volume? What's the cost shape? |
| Clock / ordering | Timezone, skew, out-of-order events, TTL boundaries |
| Data integrity | Validation gaps, orphan records, unbounded growth |
| Observability gaps | When this breaks at 3am, what tells you where? |
| Backward compatibility | Existing callers, stored data, in-flight requests |

If the SDD touches auth, secrets, PII or a public endpoint, also spawn `takshak:security-reviewer` on the SDD in parallel and merge its findings.

**4b — Build the Risk Register** from the findings. Severity: `Critical` (data loss, security, silent corruption), `High` (user-visible failure, no recovery path), `Medium` (degradation, operational pain).

**4c — Revise** the SDD to mitigate every `Critical` and `High`. Mitigations go in the relevant body section, not only in the register; set each row's Status to `mitigated (§<section>)`.

**4d — Re-attack** with a *new* `takshak:sdd-adversary` on the revised SDD. Converged means: all 10 classes have a verdict **and** no new `Critical`/`High`.

**Cap at 3 attack passes.** If still unconverged, tell the user plainly which risks remain unmitigated and why. Do not keep looping, and do not fake convergence. Coverage, not vibes, is the exit condition.

**4e — Record the outcome.** Once per design session (after the loop ends, converged or capped — not once per attack pass), count the final Risk Register's rows by severity, then run:
```bash
bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/tools/metrics.py" record --project "${CLAUDE_PROJECT_DIR:-$PWD}" --event adversary_pass --data '{"passes": <N>, "risks_critical": <count>, "risks_high": <count>, "risks_medium": <count>, "converged": <true|false>, "sdd_path": "<sdd path>"}'
```
This is what the dashboard and `/takshak:doctor`-adjacent metrics use to show catch rate and convergence — record it whether or not the SDD converged.

### Step 5 — Write checkpoint
Write `.claude/checkpoint.json`:
```json
{
  "timestamp": "<ISO-8601 UTC, with offset>",
  "feature_branch": "<current git branch>",
  "active_skill": "design",
  "active_task_id": "",
  "phase": 3,
  "phase_label": "SDD hardened (<N> attack passes, <M> risks mitigated)",
  "files_modified_this_session": ["<sdd path>"],
  "next_step": "Run /takshak:breakdown <sdd path>",
  "pending_decisions": [],
  "notes": ""
}
```

### Step 6 — Save SDD
`sdd_path` from `.claude/framework.json` (default `docs/sdd`) → `<sdd_path>/<feature-slug>.md`. Confirm the path with the user before the first write (Step 4a).

### Step 7 — Capture design decisions (memory)
For each non-obvious architectural choice ("polling over webhooks", "DynamoDB over RDS"), write a `project` memory in the memory directory from Step 1.5: `design_<slug>_<decision>.md` with frontmatter (`name`, `description`, `type: project`) and a body of the decision, **Why:**, **How to apply:**. Add a line to `MEMORY.md`.

Update `last_memory_write` in `.claude/task_state.json` (ISO-8601 UTC).

## Token Budget Rules
- Extract only the Jira fields you need
- Write the SDD to disk immediately; downstream skills read it from there
- Attack passes run in subagents; only their findings come back

## Quality Bar
- Non-Goals non-empty
- Architecture diagram shows at least 2 components; Module Map names every module the Key Flows mention
- Key Flows has a happy path per Goal and at least one failure path
- API Contracts specify every field's type and every error response (status → body → when)
- Detailed Design covers at least one module at signature level
- Risk Register has a verdict for all 10 classes; no `Critical` unmitigated
- Requirements table has an ID, a testable acceptance criterion and a priority for every row; non-functional rows have numbers
- Every significant decision (data store, sync vs async, build vs buy, consistency, auth model) has an ADR (`tools/adr.py new`) listing the alternatives actually considered; the SDD links it
- Test Plan has one row per requirement
- Open Questions are honest — never a fake "None"

## Handoff
End with the literal next command:
```
Next: /takshak:breakdown <sdd path>
```

## Downstream gate
`/takshak:breakdown` refuses an SDD with no Risk Register, an unaddressed failure class, or an unmitigated `Critical`. If implementation later proves the design wrong, `/takshak:redesign` corrects it with a recorded decision instead of quiet patching.
