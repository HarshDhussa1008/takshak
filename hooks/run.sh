#!/bin/sh
# Interpreter launcher for takshak hooks, the statusline and tools. Pure POSIX sh --
# no bashisms -- so it runs the same under `sh` or `bash`; hooks.json and the skills
# invoke it with `bash`, which Git Bash always provides on Windows (`sh` is not
# guaranteed to be on PATH there).
#
# IMPORTANT (Windows): every command string that invokes this script must use forward
# slashes only (${CLAUDE_PLUGIN_ROOT}/hooks/run.sh). Git Bash's MSYS runtime reparses the
# inherited command line itself and treats an unescaped backslash as an escape character,
# so a raw native path like D:\takshak\hooks\run.sh is silently mangled into
# D:takshakhooksrun.sh and bash fails to find the file. Claude Code's own command
# strings (as used in hooks.json and the skills here) are forward-slash already; this
# only bites a caller that builds a command string from a raw Windows path itself.
#
#   run.sh <script.py> [args...]
# Picks the first Python >= 3.10 among python3 / python / py -3, so the same hooks.json
# works on macOS (no `python`), Linux, and Windows Git Bash (where `python3` can be the
# Microsoft Store stub). The choice is cached in $CLAUDE_PLUGIN_DATA to keep hooks fast.
#
# The probe below spawns `<candidate> -c "$check"` before exec-ing the real script.
# `</dev/null` on every probe call keeps it from ever touching the real hook payload,
# regardless of how a given platform's native python.exe handles an inherited pipe.

set -u
script="$1"; shift

cache=""
if [ -n "${CLAUDE_PLUGIN_DATA:-}" ]; then
  cache="$CLAUDE_PLUGIN_DATA/python-path"
  if [ -f "$cache" ]; then
    py=$(cat "$cache")
    if [ -n "$py" ] && command -v "${py%% *}" >/dev/null 2>&1 </dev/null; then
      exec $py "$script" "$@"
    fi
  fi
fi

check='import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
for candidate in "${TAKSHAK_PYTHON:-}" python3 python "py -3"; do
  [ -z "$candidate" ] && continue
  if $candidate -c "$check" </dev/null >/dev/null 2>&1; then
    if [ -n "$cache" ]; then
      mkdir -p "$CLAUDE_PLUGIN_DATA" 2>/dev/null && printf '%s' "$candidate" > "$cache" 2>/dev/null
    fi
    exec $candidate "$script" "$@"
  fi
done

echo "[takshak] Python 3.10+ not found (tried python3, python, py -3). Set TAKSHAK_PYTHON." >&2
exit 0
