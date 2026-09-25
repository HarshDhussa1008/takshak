# Changelog

## Unreleased

### Added
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
