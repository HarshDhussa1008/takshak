"""statusLine command -- receives session JSON on stdin on every render.

1. Renders a compact status line (model, branch, context %, rate-limit %, pipeline phase).
2. Side effect, in takshak-enabled projects only: persists rate-limit headroom to
   .claude/budget.json and trips an alert at budget_alert_threshold so the Stop /
   UserPromptSubmit hooks can tell Claude to checkpoint before a breach.

The statusline is the only local surface that receives rate_limits; hooks do not. Plugins
cannot own a statusLine, so /takshak:init points .claude/settings.local.json at a copy of
this file in ${CLAUDE_PLUGIN_DATA}/statusline, which session_start.py refreshes whenever
the plugin updates. Must never raise and never be slow.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import Project, git, project_root, read_json, write_json  # noqa: E402

DEFAULT_THRESHOLD = 95.0
BAR_WIDTH = 8
HISTORY_MAX_SAMPLES = 60
HISTORY_MIN_INTERVAL_SECONDS = 60

GLYPHS = {"full": "#", "empty": "-", "sep": " | "}
if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") == "utf8":
    GLYPHS = {"full": "▓", "empty": "░", "sep": " │ "}


def bar(pct: float) -> str:
    filled = min(BAR_WIDTH, max(0, round(pct / 100 * BAR_WIDTH)))
    return GLYPHS["full"] * filled + GLYPHS["empty"] * (BAR_WIDTH - filled)


def window_state(rate_limits: dict[str, Any], key: str) -> dict[str, Any] | None:
    window = rate_limits.get(key)
    if not isinstance(window, dict) or window.get("used_percentage") is None:
        return None
    resets_at = window.get("resets_at")
    iso = datetime.fromtimestamp(resets_at, timezone.utc).isoformat() if isinstance(resets_at, (int, float)) else ""
    return {"used_percentage": float(window["used_percentage"]), "resets_at": resets_at, "resets_at_iso": iso}


def append_history(project: Project, now: datetime, context_pct: float | None, windows: dict[str, Any]) -> None:
    path = project.budget_history
    samples: list[dict[str, Any]] = []
    try:
        samples = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except (OSError, json.JSONDecodeError, ValueError):
        samples = []
    if samples and now.timestamp() - samples[-1].get("t", 0) < HISTORY_MIN_INTERVAL_SECONDS:
        return
    samples.append({
        "t": int(now.timestamp()),
        "ctx": context_pct,
        "5h": (windows.get("five_hour") or {}).get("used_percentage"),
        "7d": (windows.get("seven_day") or {}).get("used_percentage"),
    })
    tmp = path.with_suffix(".jsonl.tmp")
    try:
        tmp.write_text("\n".join(json.dumps(s) for s in samples[-HISTORY_MAX_SAMPLES:]) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def compute_alert(previous: dict[str, Any], windows: dict[str, Any], threshold: float) -> dict[str, Any]:
    tripped = sorted(
        ((n, s) for n, s in windows.items() if s["used_percentage"] >= threshold),
        key=lambda item: item[1]["used_percentage"], reverse=True,
    )
    if not tripped:
        return {"tripped": False}
    name, state = tripped[0]
    prev = previous.get("alert") or {}
    same_breach = prev.get("tripped") is True and prev.get("window") == name
    return {
        "tripped": True,
        "window": name,
        "used_percentage": state["used_percentage"],
        "threshold": threshold,
        "resets_at": state["resets_at"],
        "resets_at_iso": state["resets_at_iso"],
        # cleared on a fresh breach so the hooks speak exactly once per breach
        "acknowledged": bool(prev.get("acknowledged")) if same_breach else False,
    }


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError, OSError):
        return
    if not isinstance(data, dict):
        return

    project = Project(project_root(data))
    config = project.config() if project.enabled else {}
    threshold = float(config.get("budget_alert_threshold", DEFAULT_THRESHOLD))

    model = (data.get("model") or {}).get("display_name", "")
    context = data.get("context_window") or {}
    context_pct = context.get("used_percentage")
    context_pct = float(context_pct) if isinstance(context_pct, (int, float)) else None

    rate_limits = data.get("rate_limits") or {}
    windows = {
        name: state for name in ("five_hour", "seven_day", "spend_limit")
        if (state := window_state(rate_limits, name)) is not None
    }

    alert: dict[str, Any] = {"tripped": False}
    if project.enabled:
        alert = compute_alert(read_json(project.budget), windows, threshold)
        now = datetime.now(timezone.utc)
        append_history(project, now, context_pct, windows)
        write_json(project.budget, {
            "updated_at": now.isoformat(),
            "updated_at_epoch": int(time.time()),
            "context_window": {
                "used_percentage": context_pct,
                "size": context.get("context_window_size"),
                "exceeds_200k": data.get("exceeds_200k_tokens"),
            },
            "windows": windows,
            "alert": alert,
        })

    segments = []
    if model:
        segments.append(model)
    if branch := git(project.root, "rev-parse", "--abbrev-ref", "HEAD", timeout=2):
        segments.append(branch)
    if context_pct is not None:
        segments.append(f"ctx {bar(context_pct)} {context_pct:.0f}%")
    for label, key in (("5h", "five_hour"), ("7d", "seven_day"), ("spend", "spend_limit")):
        if key in windows:
            pct = windows[key]["used_percentage"]
            segments.append(f"{label} {pct:.0f}%{' !' if pct >= threshold else ''}")
    if project.enabled:
        checkpoint = read_json(project.checkpoint)
        if skill := checkpoint.get("active_skill"):
            task = checkpoint.get("active_task_id") or ""
            segments.append(f"/{skill} p{checkpoint.get('phase', '?')}{f' t{task}' if task else ''}")
    if alert.get("tripped"):
        segments.append(f"CHECKPOINT NOW - {alert['window']} at {alert['used_percentage']:.0f}%")

    print(GLYPHS["sep"].join(segments))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 -- a statusline must never raise
        pass
