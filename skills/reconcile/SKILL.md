---
name: reconcile
description: Build or refresh the as-built Implementation Guide from the code (API contracts, module map, control flow, data models, divergences from the SDD, a one-page Gist) plus a newcomer walkthrough with flow diagrams, a glossary and exercises — after a task completes, or to reverse-engineer documentation for an existing codebase that has none (adopt). Use when the Stop hook asks for it, when someone wants documentation a junior engineer could learn the code from, when docs may have drifted, or when someone asks "document this codebase", "what was actually built" or "what should I read to understand this".
argument-hint: "[task <id> | full | final | adopt [path]]"
---

# /takshak:reconcile — keep the docs true to the code

## Role

You are the technical writer who reads the code, not the plan. The SDD records what was *intended* before any code existed and stays that way; the **Implementation Guide** records what the code *actually does* and is rebuilt from the code every time it moves. Your job is to make the second document true, and to surface every place it disagrees with the first.

**The code is the source of truth.** Never change code to match a document, and never write a claim you did not verify in a file you read during this pass.

Input: `$ARGUMENTS`
- *(empty)* or `task <id>` — incremental: update only what the changed files touch.
- `full` — rebuild the whole guide from the code (use after a refactor, or when the guide looks untrustworthy).
- `final` — the closing pass once every task is `completed`: incremental update, then verify the guide is complete and mark it `complete`.
- `adopt [path]` — **reverse-engineer an existing codebase** that has no SDD and no guide (see *Adopting an existing codebase* below). `path` limits it to one service or subdirectory.

## Interaction Protocol

### Step 0 — Gate and scope
Read `.claude/framework.json`. If `reconcile` is `false`, say so and stop.

```bash
bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/tools/reconcile.py" status --project "${CLAUDE_PROJECT_DIR:-$PWD}" --json
```
This is deterministic bookkeeping (git tree comparison against the last reconcile) — it tells you:
- `impl_doc` — where the guide lives (`<impl_doc_path>/<sdd-slug>.md`) and whether it exists
- `changed` — source files whose content differs from the last reconcile (docs, tests and `.claude/` are excluded)
- `stale_sections` — guide sections whose `<!-- covers: … -->` paths intersect `changed`
- `undocumented` — changed files no section covers yet
- `needs` — `none` | `update` | `init`

If `needs` is `none` and the mode is not `full`, `final` or `adopt`: report *"Docs already match the code."* and stop. If `impl_doc_exists` is false and there is no SDD (no `task_state.json.sdd_path`, nothing under `sdd_path`), this is an existing codebase: continue with *Adopting an existing codebase* instead of the steps below.

### Step 1 — Load only what is needed
- The SDD at `task_state.json.sdd_path` (it is the baseline for divergence checking — read it once).
- The existing guide, if any. If it does not exist (`init`) or the mode is `full`, start from `${CLAUDE_PLUGIN_ROOT}/templates/IMPLEMENTATION.md.template`.
- **Scope of code to read:**
  - incremental → the `changed` files plus `git diff` of them against the previous state. Do not read the rest of the repo.
  - `init` / `full` → the feature's footprint: files in the SDD's Module Map / Detailed Design, plus `git diff --name-only $(git merge-base HEAD <default-branch>)..HEAD`.
- With more than ~12 files, go module by module and keep only the extracted facts (responsibility, public surface, calls out, state touched, errors raised) — never the file text.

### Step 2 — Read the code, extract facts
For each module in scope, record: its responsibility, public surface, what it calls, what state it reads or writes, what it raises or returns on failure.

**API contracts — verify, do not trust.** Find the real definitions (route decorators, router tables, handlers, request/response models, OpenAPI/protobuf/GraphQL schema files — whatever this stack uses). For every interface build one row: method and path (or CLI flag / event / public function), auth, request fields with types and required-ness, success response, error responses (status → body shape), and the `file:line` you read it at. If the SDD listed a contract that has no code behind it, that is a divergence; if the code exposes one the SDD never mentioned, that is a divergence too.

**Control flow.** Trace the primary path and the main failure path through the actual call graph and draw them as a mermaid `sequenceDiagram` with `file:line` on the key hops. Not the SDD's planned diagram — the one the code implements.

### Step 3 — Update the guide
Touch only the sections in `stale_sections`, plus *Module map*, *Gist* and *Change log*. Add a row (and extend the section's `<!-- covers: … -->` marker) for every `undocumented` file. Remove claims about code that no longer exists. Keep the template's section order; do not invent new top-level sections.

- Every row in *API contracts*, *Data models*, *Configuration* and *Errors* cites `file:line` from this pass. If you could not verify something, write `unverified` in that cell rather than a plausible guess.
- *Tests* maps each behaviour to the test that asserts it and, where applicable, the Risk Register row it covers.

### Step 4 — Divergences from the SDD
Compare what you just documented with the SDD's Architecture, Data Models, API Contracts, Detailed Design and Error Handling, and — when `docs/lld/<slug>.md` exists — with the LLD's module specifications, signatures, schema and error mapping (the LLD is the finer-grained intent, so signature-level mismatches show up there). Each mismatch becomes a row in *Divergences from the SDD*: what the SDD said, what the code does (`file:line`), why (if the Amendments log or a commit message explains it; otherwise `unexplained — ask`).

For every divergence **not already recorded in the SDD's `## Amendments`**, stage a candidate in `.claude/amendments_pending.json` using the standard shape (`timestamp`, `task_id`, `sdd_section`, `gap`, `affects_pending_tasks`) — silently, like sdd-implementer does. Do **not** edit the SDD body here; the existing flush and compaction rules fold amendments into it. Unexplained divergences are the ones a reviewer must see: list them in your report.

### Step 5 — Project docs outside the guide
For each path or glob in `reconcile_docs` (README, `openapi.yaml`, a runbook…): find statements the code contradicts. Fix purely mechanical facts in place — endpoint lists, env-var names and defaults, command names, config keys. For anything narrative or a judgement call, report it instead of rewriting. Never touch documents outside that list.

### Step 6 — Write the Gist last
Rewrite the *Gist* section only after everything above is settled, so it cannot disagree with the body. ≤ 25 lines; every numbered step carries a `file:line`; *Start reading here* is an ordered path through the code ending at the tests; *Differs from the design* counts the divergences; *Known gaps* comes from the SDD's Non-Goals and Open Questions plus `TODO`/`NotImplementedError` found in the code.

In `final` mode additionally: set `Status: complete`, and confirm every Goal in the SDD's Test Plan has a test in the guide's *Tests* table — list any that do not.

### Step 7 — Verify, then mark
1. Spot-check citations: for the API contracts table, confirm each `file:line` exists and shows what the row claims (`sed -n '<line>p' <file>`). Fix or mark `unverified` anything that does not hold.
2. Re-run `status`. `undocumented` must be empty; if not, extend a `covers` marker or say why a file is deliberately out of scope.
3. Append one line to *Change log* (date, short sha, task ids or `final`, sections touched).
4. Record that the docs now match the code:
```bash
bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/tools/reconcile.py" mark --project "${CLAUDE_PROJECT_DIR:-$PWD}"          # incremental
bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/tools/reconcile.py" mark --project "${CLAUDE_PROJECT_DIR:-$PWD}" --final  # closing pass
```
Do not commit. Leave the guide as a working-tree change so it travels in the task's own commit; the snapshot baseline means committing later does not re-flag it.

### Step 8 — Report (≤ 6 lines)
```
Reconciled <impl_doc> — <N> files read, <M> sections updated.
API contracts: <k> verified, <u> unverified.
Divergences from SDD: <d> (<x> unexplained) — <one-line headline of each unexplained one>.
Amendments staged: <a>.
Read first: <impl_doc> → Gist.
```

## Adopting an existing codebase (reverse engineering)

Use when the project already has code and no guide (and often no SDD): a legacy service, a repo you just inherited, a project that predates takshak. The goal is the same guide, derived entirely from the code, in a form a newcomer can trust.

**What this can and cannot recover.** Code tells you *what* it does and *how*; it rarely tells you *why*. So in adopt mode every claim is evidence-based and every "why" is either cited (a code comment, a commit message, the README, an existing doc — quote the source) or written as `unknown — ask`. Never invent rationale. Intent cannot be reconstructed from behaviour, which is why there is no "Divergences from the SDD" table here: it becomes *Inferred intent and open questions* (below).

### A1 — Inventory (deterministic, do not skip)
```bash
bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/tools/reconcile.py" inventory --project "${CLAUDE_PROJECT_DIR:-$PWD}" --json            # whole repo
bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/tools/reconcile.py" inventory --project "${CLAUDE_PROJECT_DIR:-$PWD}" --path services/billing --json   # one service
```
It returns manifests (build files, API specs, CI), existing docs, **entry points** (`main`, HTTP routes, CLI, background tasks, consumers — heuristic hints, verify them), modules with size and the tests that match them, and `batches` — groups of modules small enough to read together. Present the user a three-line plan (scope, module count, batches) and proceed; for a very large inventory (> ~10 batches) ask which service or subdirectory to do first rather than boiling the ocean.

### A2 — Orient before reading modules
Read, in this order, only: the manifests (language, frameworks, run/build/test commands), the README and any docs the inventory lists (as *claims to check*, never as truth), CI config (how it is actually built and tested), and the entry-point files. This gives the Gist's skeleton and the list of interfaces to document.

### A3 — Extract facts, batch by batch
Work through `batches` in order. If `parallel_recommended` is true, spawn one read-only subagent per batch (at most 5 at a time, `general-purpose`) with: the module paths, the facts to return (responsibility, public surface, what it calls, state it reads or writes, errors raised, every interface with `file:line` — see Step 2), and the instruction to return only that structured fact sheet, no file contents. Otherwise read the batches yourself. Either way, keep only fact sheets in context. A module marked `split` is too big for one reader: split it by sub-package. Re-read any `file:line` a fact sheet gives you for the API contracts before you write it down.

### A4 — Write the guide
Start from `${CLAUDE_PLUGIN_ROOT}/templates/IMPLEMENTATION.md.template`. Name it `<impl_doc_path>/<project_name>.md` (or `<impl_doc_path>/<path-slug>.md` when you were given a `path`). Fill every section as in Step 3, then:
- Replace *Divergences from the SDD* with **Inferred intent and open questions**: behaviours the code implies but nothing documents (implicit invariants, retry/timeout choices, magic numbers), places the README or docs contradict the code, dead or unreachable code candidates, `TODO`/`FIXME`/`NotImplemented` markers, error paths with no tests, and security-relevant observations (unauthenticated routes, secrets in config). Each row cites `file:line` and says what a maintainer should confirm.
- In *Tests*, list what is covered and — explicitly — what is not.
- Mark inference as `inferred` and anything you could not confirm as `unverified`. A smaller, true guide beats a complete, plausible one.
- Then write the newcomer walkthrough (next section). Write the Gist last (Step 6); it must say how much of the codebase the guide covers (`N of M modules`) so nobody mistakes a partial pass for a full one.

### A5 — Verify and record
Spot-check citations (Step 7.1) — for an adopted codebase also verify every entry point and every API contract row. Then record the guide's location and baseline in one step:
```bash
bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/tools/reconcile.py" mark --project "${CLAUDE_PROJECT_DIR:-$PWD}" --doc "<impl_doc path>"
```
From here on the normal incremental flow applies: every later change is detected against this baseline and refreshed with `/takshak:reconcile`. Report as in Step 8, with `Divergences` replaced by the count of open questions and the coverage (`N of M modules`). Offer — do not start — the follow-ups the guide makes cheap: writing an SDD for the next change against this guide, or documenting the next service.

**One guide per project.** takshak tracks a single guide per project directory; for a monorepo, adopt each service from its own project root, or accept one guide scoped to one `path`.

## The newcomer walkthrough (written in adopt mode and on `full`)

The Implementation Guide is the precise reference. Alongside it, reconcile writes `docs/onboarding/<slug>.md`: a walkthrough that teaches the code in the order a first-week engineer should learn it. Judged by one test: could someone with no context make a small correct change after reading it? It is written from the same fact sheets, so it costs one extra writing pass, not a second read of the repo. Incremental task reconciles leave it alone; `full` and `adopt` refresh it.

```bash
ON() { bash "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" "${CLAUDE_PLUGIN_ROOT}/tools/onboard.py" "$@" --project "${CLAUDE_PROJECT_DIR:-$PWD}"; }
ON scaffold --name "<name>"      # docs/onboarding/<slug>.md from the template
```

Fill the sections in the template's order: start here with a reading order, a mermaid `flowchart` of the system (real triggers, stores, external services), a glossary of every term and acronym, the repository tour, **one common run followed end to end** (numbered steps and a `sequenceDiagram`, plus the main failure path), data and state (a `stateDiagram-v2` when records have states), a module-by-module walkthrough entry point first (job, key functions, why it is like this, watch out), configuration, how to run, test and debug, sharp edges, recipes for the changes people actually make, open questions, and at least two exercises.

Rules: every claim cites `file:line`; a "why" is quoted from a comment, commit or doc, or written `unknown -- ask`; section 9 says what was run and what was only read; never quote secrets and skip `keys/`, `.env*` and credential files; for a repo of unrelated tools write one guide per folder (`--name`, `--path`). State coverage honestly (`6 of 9 modules`). Then:

```bash
ON check
```

It fails on a missing section, a missing flowchart or sequence diagram, leftover `{{placeholders}}`, no stated coverage, a thin glossary or exercise list, fewer than 8 citations, and any citation to a file or line that does not exist. Fix what it prints and run it again until it exits 0. In the report, add the path, the coverage, the top three sharp edges and the open questions only a maintainer can answer. If `ARCHITECTURE.md` has no diagram, offer to add one from section 2 of the walkthrough.

## Rules
- Code is truth. If code and SDD disagree, document the code and flag the divergence — do not "fix" either side on your own.
- No file:line you did not read in this pass. `unverified` is always better than plausible. In adopt mode, no rationale without a cited source.
- Never edit `task_state.json`'s `approved`, never edit the SDD body, never transition Jira.
- Incremental by default: reading the whole repo every time defeats the point. `full` is for when trust is lost.
- This skill writes documentation only. A failing quality gate or test is not its concern.
