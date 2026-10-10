# Changelog

## Unreleased

### Added
- **Newcomer walkthrough, inside `/takshak:reconcile`** (`adopt` and `full`): `docs/onboarding/<name>.md` with a system flowchart, glossary, reading order, one run followed end to end (sequence diagram + failure path), data and state, module-by-module tour, config, run/debug, sharp edges, change recipes and exercises. `tools/onboard.py check` fails on missing sections or diagrams, leftover placeholders and any `file:line` citation that does not exist. `ARCHITECTURE.md` now scaffolds with a system-context diagram. New config key `onboarding_path`.
- `/takshak:start` + `hooks/_flow.py` + `tools/flow.py` — the pipeline as a state machine. The next step and who must move (claude / user) are derived from files on disk, shown at session start and on Stop, and followed by one skill; `autopilot: "on"` nudges Claude onward between steps. Human gates (decisions, approval, design change, shipping) are never skipped.
- **Staged design.** `/takshak:design` is now nine stages with code-checked exits: discovery notes, requirements with IDs, ADRs, HLD with threat model and rollout/rollback, hardening, **freeze**, LLD, a 24-check **readiness gate** (`tools/design.py readiness`) that `/takshak:breakdown` requires, and a self-contained **reviewer pack** (`tools/design.py pack`). New `/takshak:lld` derives the low-level design from the frozen design only; editing the SDD or an accepted ADR after the freeze fails the gate, and re-freezing a changed design needs a recorded `--reason` (shown in the reviewer pack). SDD, LLD, design-notes, ADR, ARCHITECTURE and CONVENTIONS templates.
- **ADRs** (`tools/adr.py`): numbered records with alternatives, status, supersession links and a structural check.
- **Requirement traceability** (`tools/trace.py`): SDD requirement IDs ↔ task `requirements` ↔ `# covers: R1` test markers; plan and ship gates fail on a requirement with no task or test and on tasks with no requirement.
- **`/takshak:redesign` + `tools/design_change.py`**: a design flaw found in implementation pauses source edits, becomes a superseding ADR, has only its delta attacked, and needs the user's own "approved" (denied if Claude writes it, via any tool) before the SDD, tasks and tests change; the design is re-frozen and the LLD regenerated.
- **Structure and architecture rules** (`tools/structure.py`): canonical docs layout recognised by heading, naming/orphan/stray-file checks, a gate that refuses a document written to the wrong place, and fitness rules in `.claude/fitness.json` (forbidden imports, layers, no-cycles, max lines; Python via `ast`, JS/TS by pattern) with a baseline ratchet and a `fitness_gate` hook that reports violations at the moment of the edit.
- New settings: `adr_path`, `lld_path`, `notes_path`, `review_path`, `design_readiness_gate`, `structure_gate`, `fitness_gate`, `layout_extra_dirs`, `autopilot`. New runtime state (gitignored): `design_changes.json`, `flow_state.json`.
- `/takshak:work` + `tools/work.py` + `hooks/_work.py` — **work items for every change**. A request that changes code is classified into a lane (`trivial`, `bug`, `change`, `feature`, `solution`) and opened as a work item before any edit; the PreToolUse gate now also refuses source edits outside an approved plan without one, and covers Bash writes (redirects, `tee`, `sed -i`, `cp`/`mv`, `git apply`). A prompt hook points code-changing requests at the skill; session start states the rule; the Stop hook reminds about an open item.
- **Jira tickets sized by the work**: solution → Epic, feature → Story, bug fix / self-contained work → Task (Bug where the project has one), small edit → Subtask (promoted to Task without a parent; ambiguity rounds up). Types are resolved against the project's real issue types, not assumed.
- **Confirm first, then remember**: the first ticket in a project discovers its issue types and has the user confirm project key, type mapping and auto-mode; `.claude/jira_profile.json` remembers it (shareable). With auto-mode on, later tickets are created without asking; otherwise each is proposed first. Jira being unreachable never blocks work (the ticket is deferred).
- New settings: `work_item_gate`. New runtime state: `work_items.json` (gitignored).
- `/takshak:reconcile [task <id> | full | final]` — rebuilds an as-built **Implementation Guide** (`docs/implementation/<slug>.md`) from the code: API contracts verified at `file:line`, module map, control flow derived from real call paths, data models, configuration, error behaviour, tests, a *Divergences from the SDD* table (each also staged as an SDD amendment), and a one-page *Gist* that answers "what do I read to understand what was built".
- `/takshak:reconcile adopt [path]` — reverse-engineer an existing codebase with no SDD: `tools/reconcile.py inventory` maps manifests, modules, entry points/routes, tests and existing docs into readable batches; the guide is written from the code with `inferred`/`unverified` markers and an *Inferred intent and open questions* section instead of SDD divergences. A project with code but no guide now reports `needs: init` instead of "in sync".
- Drift detection in code, not prompts: `hooks/_reconcile.py` compares git tree snapshots (so committing doesn't re-flag reconciled files), `tools/reconcile.py status|check|mark`, a silent `reconcile_tracker.py` PostToolUse hook, and a Stop-hook request for a reconcile when a task completes or `reconcile_edit_threshold` files change.
- `/takshak:ship` Gate 0 fails (hard stop on prod) when the Implementation Guide is behind the code; the dashboard review verdict lists it as a blocker; session start points new sessions at the guide.
- `/takshak:design` SDDs now include a **Module Map** and **Key Flows** (sequence diagrams for each Goal and the main failure path), and API Contracts specify every field's type and every error response.
- New settings (auto-migrated into existing projects): `reconcile`, `reconcile_edit_threshold`, `impl_doc_path`, `reconcile_docs`.

- Pipeline metrics: `.claude/metrics.jsonl` (append-only, capped), `tools/metrics.py` CLI, and `quality_gate`/`approval`/`adversary_pass`/`ship` events recorded automatically.
- Dashboard: pipeline stepper, a "ready for review / blocked on X" verdict banner, adversary-pass history, and a pipeline-metrics tile grid.
- `/takshak:review [sdd | slug]` — guided, section-by-section walkthrough of a hardened SDD for a reviewer, ending in an explicit verdict; never touches `task_state.approved`.
- `evals/` — a `claude plugin eval` suite (breakdown gate, self-approval denial, quality-gate plumbing), wired into CI as an advisory job.
- Renamed the plugin from `shipwright` to `takshak` (naming collision with several unrelated same-niche projects). No behavior change; commands are now `/takshak:*` and the env override is `TAKSHAK_PYTHON`.

## 2.0.0 — plugin release

### Fixed (hooks were silently not working in 1.x)
- Hooks now read their input as JSON on stdin. 1.x read `CLAUDE_TOOL_INPUT` / `CLAUDE_SESSION_ID`, which Claude Code never sets: the quality gate never ran, and session-start context fired once per repository lifetime.
- Stop and PostToolUse output now reaches Claude (`decision: block`, `additionalContext`). 1.x printed to stdout, which only reaches the debug log for those events, and marked the budget alert acknowledged, which also silenced the UserPromptSubmit fallback.
- Hook timeouts and statusline `refreshInterval` are seconds (1.x used milliseconds: 5000 s hooks, an 8-hour refresh).
- Memory directory is taken from the session transcript path; 1.x derived the folder name incorrectly on Windows and Linux.
- Timestamps without a UTC offset no longer crash hooks.
- Drift detection no longer treats any commit containing a digit as mapped to a task.
- Skills referenced commands that did not exist (`/review`, `/remember`, `/resume`, `/checkpoint`) or that Claude cannot run (`/compact`).
- `/takshak:ship` uses `project_typecheck_command` instead of hard-coded mypy, and Jira transitions come from `jira_transitions`. Epic children use the `parent` field instead of `customfield_10014`.

### Added
- Claude Code plugin + marketplace packaging; team rollout with auto-update; statusline and config auto-sync at session start.
- Enforced approval gate: source edits blocked until the user replies "approved"; Claude cannot approve its own plan.
- Reviewer agents: `sdd-adversary`, `code-reviewer`, `security-reviewer`, `test-auditor`.
- Skills: `init` (stack detection, migration, team settings), `doctor`, `resume`, `remember`, `checkpoint`. YAML frontmatter on all skills; `/takshak:ship` is never auto-invoked.
- Tasks record which risks they mitigate; `test-auditor` traces mitigations to tests.
- Test suite driving every hook through its real stdin/stdout contract; CI on Linux/macOS/Windows and `claude plugin validate`.
