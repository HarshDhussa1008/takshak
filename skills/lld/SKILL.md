---
name: lld
description: Derive the low-level design (exact signatures, algorithms and state machines for non-trivial logic, schema and migration steps, error mapping, configuration, per-module test mapping) from a frozen SDD and its accepted ADRs, then stamp it so the readiness gate can tell it is current. Use after /takshak:design freezes the design, or when the readiness check says the LLD is missing or stale.
argument-hint: "[sdd path]"
---

# /takshak:lld — the low-level design, derived from the frozen design

## Role

You are the engineer who turns a finished design into something a developer can implement without making a single architectural choice. The LLD **decides nothing new**: every line follows from the frozen SDD or an accepted ADR. If writing it forces a decision the design never made, the design has a gap — that is a finding, not something to quietly settle here.

Input: `$ARGUMENTS` (the SDD path; default: `task_state.json.sdd_path`, else the only file in `sdd_path`)

```bash
DS() { bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/tools/design.py" "$@" --project "${CLAUDE_PROJECT_DIR:-$PWD}"; }
```

## Step 1 — Gate: is the design frozen?
`DS status --json`. If `freeze.frozen` is false: stop — *"The design is not frozen. Finish `/takshak:design` (Stage 6) first."* If `freeze.intact` is false the SDD or an accepted ADR changed after the freeze: stop and run `/takshak:redesign` (which re-freezes deliberately). The LLD must never be derived from a design that is still moving.

## Step 2 — Load
Read the SDD (Requirements, Module Map, Key Flows, Data Models, API Contracts, Detailed Design, Error Handling, Observability, Security), the accepted ADRs, `ARCHITECTURE.md` / `CONVENTIONS.md` for naming and layering, and the existing code the modules touch. Read the code that exists; do not assume its shape.

## Step 3 — Write `docs/lld/<slug>.md` from `${CLAUDE_PLUGIN_ROOT}/templates/LLD.md.template`
- **Files and layout** — every path that will be created or changed, matching the Module Map and the project's conventions.
- **Module specifications** — one entry for **every module in the Module Map**: responsibility, the exact public interface with types, invariants and pre/postconditions, errors raised or returned, dependencies with the exact functions used. For any non-trivial logic (concurrency control, retries, state transitions, algorithms) give pseudocode or a state table precise enough to implement and to test.
- **Sequences** — the SDD's Key Flows again, at function level (`file:function(args) → result`), including the failure path.
- **Data schema** — DDL / schema / migration steps **in order**, with how each step is reversed (the SDD's rollback plan must be implementable from this).
- **Error mapping** — condition → internal error → what the caller sees → retry policy.
- **Configuration** — every setting, default, effect, and where it is validated.
- **Test mapping** — per module: the tests to write, the requirement IDs they prove (`# covers: R1`), and the Risk Register rows they mitigate. Every requirement must appear.

Replace every `{{placeholder}}`. Keep it as short as the design allows; length is not rigor.

## Step 4 — Stamp and verify
```bash
DS stamp-lld          # records which freeze this LLD was derived from
DS readiness          # the LLD checks must now pass: derived from the frozen design, specifies every module
```
If it reports a module with no spec or a leftover placeholder, fix the LLD and re-run.

## Step 5 — Hand back
Report in two lines: modules specified, and any place where deriving the LLD exposed a gap in the design (with the section). A gap means `/takshak:redesign`, not a note. Return control to `/takshak:design` (Stage 8) or to the user.

## Rules
- Never edit the SDD or an ADR from here; the freeze exists to stop exactly that.
- Never invent an interface the SDD did not name; add it to the list of gaps instead.
