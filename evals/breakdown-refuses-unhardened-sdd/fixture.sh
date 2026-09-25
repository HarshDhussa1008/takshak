#!/bin/sh
# Harness-run scaffold (outside the agent's own tool sandbox): seeds a takshak project
# with an SDD that has no Risk Register, so /takshak:breakdown must refuse it (the same
# gate it enforces against a real unhardened SDD -- see skills/breakdown/SKILL.md Step 1).
set -eu
mkdir -p .claude docs/sdd
cat > .claude/framework.json <<'EOF'
{"schema_version": 2, "project_name": "evalproj", "approval_gate": true, "sdd_path": "docs/sdd", "gate_extensions": [".py"], "test_command": "echo ok", "lint_command": null, "typecheck_command": null, "project_typecheck_command": null, "build_command": "echo ok", "jira_integration": false, "slack_integration": false, "budget_alert_threshold": 95, "jira_transitions": {}}
EOF
cat > .claude/task_state.json <<'EOF'
{"feature_branch": "", "parent_jira_key": null, "sdd_path": null, "approved": false, "approved_at": null, "approved_by": null, "tasks_created_at": null, "retro_offered": false, "last_memory_write": null, "last_test_run": null, "tasks": []}
EOF
echo '{}' > .claude/checkpoint.json
echo '{"candidates": [], "style_candidates": []}' > .claude/amendments_pending.json
echo '{}' > .claude/anti_pattern_registry.json
cat > docs/sdd/unhardened.md <<'EOF'
# SDD: Rate limiter

## Overview
Adds a rate limiter to the API gateway.

## Goals
- Reject requests over the configured quota

## Non-Goals
- Distributed rate limiting across regions

## Architecture
```mermaid
graph LR
  Client --> Gateway --> Backend
```

## Test Plan
| Goal | Test | Type |
|------|------|------|
| Reject over-quota requests | test_rate_limit_rejects | unit |
EOF
