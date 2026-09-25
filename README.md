# takshak

A structured engineering pipeline for [Claude Code](https://code.claude.com), packaged as a plugin. Design → adversarial review → break down → implement → ship, with Jira sync, an enforced approval gate, a quality gate on every edit, rate-limit protection, a live dashboard with pipeline metrics, and a guided review for anyone walking in cold.

```
/takshak:design  →  /takshak:breakdown  →  "approved"  →  implement  →  /takshak:retro  →  /takshak:ship
 (SDD + hardening)      (tasks + Jira)            (you, not     (bug loop)     (memories)            (gates + Jira
                                                    Claude)                                            transitions)
```

**The design principle:** you type commands only at the moments you deliberately step back. Everything else — feeding lint/type findings back after each edit, keeping the SDD honest, catching drift, protecting work before a rate-limit breach, re-injecting past lessons — runs in hooks while you stay in the implementation loop.

## Install

```bash
# in Claude Code
/plugin marketplace add HarshDhussa1008/takshak
/plugin install takshak@takshak
/takshak:init
```

or from a shell: `./install.sh --project-dir /path/to/project` (Windows: `.\install.ps1 -ProjectDir C:\path\to\project`).

`/takshak:init` detects your stack (Python, TypeScript/JavaScript, Go, Rust), writes `.claude/framework.json`, seeds state files and `CLAUDE.md`, wires the budget statusline, and asks only for what it cannot detect (deploy command, Jira project, transition names). `/takshak:doctor` checks that every configured command actually runs.

### Roll it out to a team

```
/takshak:init --team
```

This adds the marketplace (with `autoUpdate: true`) and `enabledPlugins` to the repo's `.claude/settings.json`. Commit it. When a teammate trusts the folder, Claude Code offers them the one-line install; from then on they receive every update automatically. For org-wide enforcement, put the same keys in [managed settings](https://code.claude.com/docs/en/plugin-marketplaces) and restrict sources with `strictKnownMarketplaces`. To distribute from an internal fork, pass `--marketplace-repo your-org/takshak`.

### Staying in sync

| Piece | How it updates |
|---|---|
| Skills, agents, hooks, dashboard | Plugin marketplace. `plugin.json` has no `version`, so every commit to the marketplace repo is a new version. Auto-update is on for team installs; individuals enable it once in `/plugin` → Marketplaces. Pin releases by adding `version` to `plugin.json`. |
| Budget statusline | Plugins cannot own a statusline, so init points `settings.local.json` at a copy in the plugin's data directory. The SessionStart hook refreshes that copy whenever the plugin changes. |
| `framework.json` | New settings are added with defaults at session start. Your values are never overwritten. |
| `.claude/.gitignore` | New runtime-state patterns are appended at session start. |

## Commands

| Command | What it does |
|---|---|
| `/takshak:design <req \| KEY>` | Writes an SDD, then the `sdd-adversary` agent attacks it in a fresh context across 10 failure classes. Revise and re-attack, max 3 passes. |
| `/takshak:breakdown <sdd>` | Refuses unhardened SDDs. ≤ 8 tasks, dependency-ordered, each tied to the risks it mitigates. Creates Claude Code tasks and Jira issues. |
| *reply* `approved` | A hook sets the approval flag from **your** message. Until then source edits are denied, and Claude cannot approve its own plan. |
| `sdd-implementer` | Task-by-task implementation. Stays in the bug loop, silently stages SDD amendments, checkpoints continuously. |
| `/takshak:retro` | Plan vs. actual, four questions, memories, CLAUDE.md lessons, Jira comment (never a transition). |
| `/takshak:ship <env>` | Docs → tests → types → review (`code-reviewer`, `test-auditor`, and `security-reviewer` when relevant) → build → Jira transitions → Slack. Never auto-invoked. |
| `/takshak:dashboard [serve]` | Read-only visual state: pipeline stage, review verdict, risks, tasks, amendments, pipeline metrics, budget trend, patterns, inbox. |
| `/takshak:review [sdd \| slug]` | Guided walkthrough of a hardened SDD for a reviewer who didn't write it — section by section, ending in an explicit verdict. |
| `/takshak:resume` | Continue from the checkpoint after an interruption or a rate-limit reset. |
| `/takshak:checkpoint [show\|save\|clear\|clear-plan]` | Manage the checkpoint or abandon a plan. |
| `/takshak:remember [text]` | Write a typed memory that re-enters at session start and design time. |
| `/takshak:init`, `/takshak:doctor` | Set up, migrate, diagnose. |

## Hooks (automatic)

| Hook | Event | Reaches Claude via | What it does |
|---|---|---|---|
| `session_start.py` | SessionStart (startup, resume, clear, compact) | stdout context | Relevant memories, plan state, interrupted checkpoint, budget-reset status; syncs statusline and config |
| `prompt_submit.py` | UserPromptSubmit | stdout context | Captures your "approved"; relays an undelivered budget breach |
| `approval_gate.py` | PreToolUse (Edit/Write/MultiEdit/NotebookEdit) | `permissionDecision: deny` | Blocks source edits while the plan awaits approval; blocks self-approval |
| `quality_gate.py` | PostToolUse (Edit/Write/MultiEdit) | `additionalContext` | Runs your lint + type commands on the edited file and returns the findings; tracks recurring codes |
| `stop_checklist.py` | Stop | `decision: block` (Claude must act) / `systemMessage` (you should know) | Budget breach, staged amendments, stale checkpoint; drift, open tasks, uncommitted files, retro offer |
| `budget_sentinel.py` | statusLine | budget.json → the hooks above | The only surface that sees `rate_limits`; trips at `budget_alert_threshold` |

Every hook is a no-op in projects without `.claude/framework.json`, so installing the plugin at user scope never affects other repos.

## How it stays honest

- **Design is hardened before code exists.** The adversary sees only the SDD, so it cannot converge on the author's reasoning. Coverage of all 10 classes is the exit condition.
- **The human checkpoint is enforced.** The plan cannot be approved by the agent, and code cannot be written before approval.
- **The SDD is a living document.** Design gaps found while fixing bugs are staged as amendments, folded in at the next pause, and compacted after 5. Amendments mark affected tasks `needs_recheck`.
- **Mitigations are traceable.** Tasks record which risks they mitigate; the `test-auditor` blocks a prod ship when a Critical risk has no test that would fail without its mitigation.
- **Evolution is passive.** Corrections and recurring lint codes become memories that re-enter at design time. Nothing rewrites its own skill files.
- **One owner for Jira status.** Only `/takshak:ship` transitions tickets, using the status names in `jira_transitions`.
- **The enforcement claims are checked, not just asserted.** `evals/` runs the plugin against real Claude sessions — the breakdown gate refusing an unhardened SDD, self-approval being denied, quality-gate findings reaching Claude — and scores them, so a change that quietly breaks one of these is caught before it ships.

## Configuration

`.claude/framework.json` (created by init). The key fields:

| Field | Default | Purpose |
|---|---|---|
| `test_command` / `build_command` | detected / placeholder | Ship gates 1 and 4 |
| `lint_command` / `typecheck_command` | detected | Per-edit quality gate; `{file}` is the edited file; `null` to skip |
| `project_typecheck_command` | detected | Ship gate 2 |
| `gate_extensions` | detected | Which edits the quality gate checks |
| `approval_gate` | `true` | Enforce approval before source edits |
| `jira_integration`, `jira_project_key` | `false`, `null` | Jira sync (Atlassian remote MCP or mcp-atlassian) |
| `jira_subtask_type` | `"Subtask"` | Issue type for tasks under a non-Epic parent |
| `jira_transitions` | `{"staging": "In Review", "prod": "Done"}` | Target status per environment |
| `budget_alert_threshold` | `95` | Rate-limit % that triggers the checkpoint directive |
| `sdd_path` | `"docs/sdd"` | Where SDDs live |
| `memory_path` | `null` | Override the memory directory |

## Development

```bash
claude --plugin-dir .          # run Claude Code with this checkout as the plugin
python -m pytest -q            # hook contract, packaging and bootstrap tests
claude plugin validate .
claude plugin eval . --scaffold --allow-tools Edit,Write --ablation none   # evals/*, 3 cases
```

CI runs the tests on Linux, macOS and Windows with Python 3.10 and 3.13, plus `claude plugin validate`. A separate, advisory `eval` job runs the eval suite against real Claude sessions on push, skipped entirely unless an `ANTHROPIC_API_KEY` secret is set.

Hooks run through `hooks/run.sh` (invoked via `bash`, which Git Bash always provides on Windows), which picks the first Python ≥ 3.10 among `python3`, `python` and `py -3`. On Windows this needs Git Bash installed, and every command string that references `run.sh` must use forward slashes — Git Bash's MSYS runtime mangles a raw backslash path. Set `TAKSHAK_PYTHON` to override the interpreter.

## License

MIT
