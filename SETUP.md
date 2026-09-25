# Setup guide

## Prerequisites

| Requirement | Notes |
|---|---|
| Claude Code | CLI, desktop app or IDE extension, recent enough for plugins |
| Python 3.10+ | `python3`, `python` or `py -3` on PATH (or set `TAKSHAK_PYTHON`) |
| Git Bash (Windows) | Claude Code runs hooks through it; hooks invoke `run.sh` via `bash`. If WSL is also installed, `bash` resolved from `PATH` alone can be WSL's launcher stub (`C:\WINDOWS\system32\bash.exe`) instead of Git Bash's — a different interpreter with its own `/mnt/c/...` filesystem view that cannot open a native Windows path in any form. `/takshak:doctor` detects this and tells you to set `CLAUDE_CODE_GIT_BASH_PATH` (see below) |
| Your toolchain | Whatever `lint_command` / `typecheck_command` / `test_command` name — `/takshak:doctor` checks them |
| Jira MCP (optional) | The Atlassian remote MCP or the community `mcp-atlassian` server |
| Slack MCP (optional) | For deploy notifications |

## 1. Install the plugin

In Claude Code:
```
/plugin marketplace add HarshDhussa1008/takshak
/plugin install takshak@takshak
```
Then `/plugin` → **Marketplaces** → takshak → **Enable auto-update** (third-party marketplaces default to off).

## 2. Initialise a project

Open Claude Code in the project and run `/takshak:init`. It is safe to re-run.

## 3. Team rollout (optional)

`/takshak:init --team`, then commit `.claude/settings.json`. Teammates who trust the folder are offered the install, with auto-update on. To host from your org, fork the repo and use `--marketplace-repo your-org/takshak`.

## Migrating from the copy-installed version (v1)

v1 copied hooks into `.claude/hooks/` and wired them in `.claude/settings.json`. With the plugin installed those would fire twice, and several of them never worked (they read environment variables Claude Code does not set). Run:

```
/takshak:init --migrate
```

It removes the legacy hook and statusline wiring (your own hooks are kept), deletes the framework-owned `.claude/hooks/*.py`, `.claude/tools/dashboard.py` and `.claude/session_id`, and adds any new `framework.json` settings. Your config, state, SDDs and memories are untouched. The skills v1 copied into `~/.claude/skills/{design,breakdown,ship,retro,dashboard,sdd-implementer}` can be deleted once you use the `/takshak:*` versions.

## Jira

1. Connect a Jira MCP server.
2. In `.claude/framework.json`: `jira_integration: true`, `jira_project_key`.
3. Check `jira_transitions` matches your workflow's **status names** (`/takshak:ship` finds the transition that leads to that status).
4. Name branches with the parent key (`feature/PAY-123-rate-limit`); `/takshak:breakdown` asks if it can't find one.

The skills name the tools for both common servers (`getJiraIssue` / `jira_get_issue`, `createJiraIssue` / `jira_create_issue`, `transitionJiraIssue` / `jira_transition_issue`, `addCommentToJiraIssue` / `jira_add_comment`). Tasks under an Epic are created as Stories with the `parent` field.

## Memory

Claude Code keeps project memory in `~/.claude/projects/<project-folder>/memory/`, where `<project-folder>` is the absolute path with every non-alphanumeric character replaced by `-`. The hooks locate it from the session's transcript path, so no configuration is needed; set `memory_path` to override. `/takshak:design`, `/takshak:retro` and `/takshak:remember` write memories; the SessionStart hook re-injects the three most relevant each session.

## The dashboard

`/takshak:dashboard` writes `.claude/dashboard.html`; `/takshak:dashboard serve` serves it on `127.0.0.1:7399` with live refresh. It shows a pipeline stepper (which stage the feature is at), a one-line review verdict (ready for review / blocked on X), the risk register, adversary pass history, and pipeline metrics (quality-gate hit rate, adversary convergence, approval latency, ship pass rate) computed from `.claude/metrics.jsonl`. Files dropped in `.claude/inbox/` are listed with paths ready to reference.

For a reviewer who wants to be walked through a specific SDD rather than reading the dashboard cold, `/takshak:review` paces the same information section by section and ends in an explicit verdict.

`.claude/metrics.jsonl` is append-only and populated automatically: `quality_gate` and `approval` events are recorded directly by hooks; `adversary_pass` and `ship` events are recorded by `/takshak:design` and `/takshak:ship` as explicit steps. `run.sh tools/metrics.py summary --project DIR` prints the same rollup from a shell.

## Troubleshooting

- Something doesn't fire → `/takshak:doctor`.
- See hook activity → run `claude --debug` and look for `takshak` lines, or set `TAKSHAK_DEBUG=1` to surface hook exceptions.
- Approval gate in the way of unrelated work → `/takshak:checkpoint clear-plan`, or `approval_gate: false`.
- **Windows + WSL: hooks silently do nothing.** If WSL is installed, `bash` can resolve to its launcher stub ahead of Git Bash's — a separate interpreter that cannot see your project's native Windows path at all. Check with `Get-Command bash -All` in PowerShell; if the top result is under `System32` or `WindowsApps`, add to Claude Code's settings (`~/.claude/settings.json` or your project's `.claude/settings.json`):
  ```json
  { "env": { "CLAUDE_CODE_GIT_BASH_PATH": "C:\\Program Files\\Git\\bin\\bash.exe" } }
  ```
  (adjust the path if Git for Windows is installed elsewhere). `/takshak:doctor` checks for this and tells you the exact path to use.
