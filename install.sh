#!/usr/bin/env bash
# Install takshak as a Claude Code plugin, then set up a project.
#
#   ./install.sh [--project-dir DIR] [--team] [--local] [--repo OWNER/REPO]
#
#   --project-dir DIR  also initialise DIR (framework.json, state, CLAUDE.md, .gitignore)
#   --team             also commit-ready team settings in DIR/.claude/settings.json so every
#                      teammate is offered the plugin, with auto-update on
#   --local            register this clone as the marketplace (plugin development)
#   --repo OWNER/REPO  marketplace repo (default HarshDhussa1008/takshak; use your org fork)
#
# Re-running is safe. Updates arrive through the marketplace, not this script:
#   claude plugin marketplace update takshak   (or enable auto-update in /plugin)

set -euo pipefail
FRAMEWORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR=""
TEAM=""
LOCAL=0
REPO="HarshDhussa1008/takshak"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-dir) PROJECT_DIR="$2"; shift 2 ;;
    --team) TEAM="--team"; shift ;;
    --local) LOCAL=1; shift ;;
    --repo) REPO="$2"; shift 2 ;;
    --update) echo "--update is no longer needed: run 'claude plugin marketplace update takshak'"; shift ;;
    *) PROJECT_DIR="$1"; shift ;;
  esac
done

if ! command -v claude >/dev/null 2>&1; then
  echo "Claude Code CLI not found on PATH. Install it first: https://code.claude.com/docs/en/setup" >&2
  exit 1
fi

SOURCE="$REPO"
[[ $LOCAL -eq 1 ]] && SOURCE="$FRAMEWORK_DIR"

echo "=== takshak ==="
echo "Marketplace: $SOURCE"
claude plugin marketplace add "$SOURCE" || echo "  (marketplace already added)"
claude plugin install takshak@takshak --scope user
echo "  Plugin installed. In an open session run /reload-plugins."
echo "  Turn on auto-update once: /plugin -> Marketplaces -> takshak -> Enable auto-update"

if [[ -n "$PROJECT_DIR" ]]; then
  echo ""
  echo "Initialising $PROJECT_DIR ..."
  bash "$FRAMEWORK_DIR/hooks/run.sh" "$FRAMEWORK_DIR/tools/bootstrap.py" init \
    --project "$PROJECT_DIR" --marketplace-repo "$REPO" --no-statusline $TEAM
  echo ""
  echo "Open Claude Code in $PROJECT_DIR and run /takshak:init once more -- it wires the"
  echo "budget statusline (needs the plugin's data dir) and asks for anything it couldn't detect."
else
  echo ""
  echo "Next: open Claude Code in your project and run /takshak:init"
fi
