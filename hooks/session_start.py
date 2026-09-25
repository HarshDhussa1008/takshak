"""SessionStart hook -- fires on startup, resume, /clear and after compaction.

Plain stdout here is added to Claude's context, so this is where accumulated memory,
the interrupted checkpoint and the plan's state re-enter a session.

It is also the auto-sync point for the pieces a plugin update cannot reach on its own:
- the statusline copy (plugins cannot own a statusLine, so we keep a stable copy in
  ${CLAUDE_PLUGIN_DATA} and refresh it whenever the plugin version changes)
- new framework.json keys (added with defaults, never overwriting yours)
- new .claude/.gitignore patterns (appended, never removed)
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    DEFAULT_CONFIG,
    Project,
    age_seconds,
    git,
    memory_dir,
    project_root,
    read_json,
    read_payload,
    run_safely,
    write_json,
)

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT_MAX_AGE_HOURS = 48
MAX_MEMORIES = 3
STATUSLINE_FILES = ("budget_sentinel.py", "_common.py", "run.sh")
STOPWORDS = {
    "the", "and", "for", "with", "into", "from", "that", "this", "add", "use", "run", "new",
    "not", "all", "any", "out", "via", "per", "its", "was", "are", "master", "main", "develop",
}


def sync_statusline() -> None:
    data_dir = os.environ.get("CLAUDE_PLUGIN_DATA")
    if not data_dir:
        return
    target = Path(data_dir) / "statusline"
    try:
        target.mkdir(parents=True, exist_ok=True)
        for name in STATUSLINE_FILES:
            src = PLUGIN_ROOT / "hooks" / name
            dest = target / name
            if src.is_file() and (not dest.is_file() or dest.read_bytes() != src.read_bytes()):
                shutil.copyfile(src, dest)
    except OSError:
        pass


def migrate_config(project: Project) -> list[str]:
    current = read_json(project.config_path)
    added = [key for key in DEFAULT_CONFIG if key not in current]
    if added:
        merged = dict(current)
        for key in added:
            merged[key] = DEFAULT_CONFIG[key]
        merged["schema_version"] = DEFAULT_CONFIG["schema_version"]
        write_json(project.config_path, merged)
    return [k for k in added if k != "schema_version"]


def merge_gitignore(project: Project) -> None:
    template = PLUGIN_ROOT / "templates" / "claude.gitignore"
    dest = project.state_dir / ".gitignore"
    try:
        wanted = [
            line.strip() for line in template.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        existing = dest.read_text(encoding="utf-8").splitlines() if dest.exists() else []
    except OSError:
        return
    have = {line.strip() for line in existing}
    missing = [line for line in wanted if line not in have]
    if missing:
        try:
            with dest.open("a", encoding="utf-8") as fh:
                if existing and existing[-1].strip():
                    fh.write("\n")
                fh.write("\n".join(missing) + "\n")
        except OSError:
            pass


def legacy_install(project: Project) -> bool:
    settings = read_json(project.state_dir / "settings.json")
    blob = str(settings.get("hooks", "")) + str(settings.get("statusLine", ""))
    return ".claude/hooks/" in blob.replace("\\", "/")


def context_keywords(project: Project, config: dict[str, Any], branch: str) -> set[str]:
    words = {branch.lower(), str(config.get("project_name", "")).lower()}
    state = read_json(project.task_state)
    for task in state.get("tasks") or []:
        words.update(re.findall(r"[a-z]{4,}", str(task.get("title", "")).lower()))
    sdd_rel = state.get("sdd_path")
    sdd_path = (project.root / sdd_rel) if sdd_rel else None
    if sdd_path is None:
        sdd_dir = project.root / str(config.get("sdd_path") or "docs/sdd")
        if sdd_dir.is_dir():
            recent = sorted(sdd_dir.rglob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
            sdd_path = recent[0] if recent else None
    if sdd_path is not None and sdd_path.is_file():
        try:
            words.update(re.findall(r"[a-z]{4,}", sdd_path.read_text(encoding="utf-8")[:1200].lower()))
        except OSError:
            pass
        words.add(sdd_path.stem.lower())
    return {w for w in words if w and w not in STOPWORDS}


def frontmatter(text: str) -> tuple[dict[str, str], str]:
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            for line in parts[1].splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    meta[key.strip()] = value.strip()
            body = parts[2]
    return meta, body


def relevant_memories(directory: Path, keywords: set[str]) -> list[tuple[str, str]]:
    if not directory.is_dir():
        return []
    scored = []
    for md_file in directory.glob("*.md"):
        if md_file.name == "MEMORY.md":
            continue
        try:
            meta, body = frontmatter(md_file.read_text(encoding="utf-8"))
            mtime = md_file.stat().st_mtime
        except OSError:
            continue
        desc = meta.get("description", "")
        snippet = next((ln.strip() for ln in body.splitlines() if ln.strip() and not ln.startswith("#")), desc)
        text = f"{desc} {snippet} {md_file.stem}".lower()
        score = sum(1 for kw in keywords if kw in text)
        if meta.get("type") in {"feedback", "user"}:
            score += 1  # style corrections apply whatever feature is in flight
        if score:
            scored.append((score, mtime, meta.get("name") or desc or md_file.stem, snippet[:140]))
    scored.sort(key=lambda m: (m[0], m[1]), reverse=True)
    return [(title, snippet) for _, _, title, snippet in scored[:MAX_MEMORIES]]


def budget_banner(project: Project) -> str | None:
    alert = read_json(project.budget).get("alert") or {}
    if not alert.get("tripped"):
        return None
    resets_at = alert.get("resets_at")
    window = alert.get("window", "rate limit")
    if isinstance(resets_at, (int, float)):
        import time

        if time.time() >= resets_at:
            return f"[BUDGET] {window} window has reset -- the prior checkpoint is safe to resume."
        return (
            f"[BUDGET] {window} still at {float(alert.get('used_percentage', 0)):.0f}% until "
            f"{alert.get('resets_at_iso', 'unknown')} -- keep this session short."
        )
    return None


def checkpoint_lines(project: Project) -> list[str]:
    data = read_json(project.checkpoint)
    if not data.get("active_skill"):
        return []
    age = age_seconds(data.get("timestamp"))
    if age is not None and age > CHECKPOINT_MAX_AGE_HOURS * 3600:
        return []
    lines = [
        f"[CHECKPOINT] Interrupted work ({str(data.get('timestamp', ''))[:16] or 'unknown time'})",
        f"  Skill: /takshak:{data['active_skill']} | Task: {data.get('active_task_id', '')}",
        f"  Phase: {data.get('phase_label') or data.get('phase', '?')}",
    ]
    if modified := data.get("files_modified_this_session"):
        lines.append(f"  Modified: {', '.join(map(str, modified))}")
    if next_step := data.get("next_step"):
        lines.append(f"  Next: {next_step}")
    if notes := data.get("notes"):
        lines.append(f"  Deferred: {notes}")
    lines.append("  The user can run /takshak:resume to continue or /takshak:checkpoint clear to discard.")
    return lines


def plan_lines(project: Project) -> list[str]:
    state = read_json(project.task_state)
    tasks = state.get("tasks") or []
    if not tasks:
        return []
    done = sum(1 for t in tasks if t.get("status") == "completed")
    lines = [f"[PLAN] {done}/{len(tasks)} tasks complete -- SDD: {state.get('sdd_path') or 'unknown'}"]
    if state.get("approved") is not True:
        lines.append(
            "  Awaiting approval: source edits are blocked until the USER replies 'approved'. "
            "Do not set the flag yourself."
        )
    recheck = [str(t.get("id")) for t in tasks if t.get("needs_recheck")]
    if recheck:
        lines.append(f"  needs_recheck (re-plan before implementing): {', '.join(recheck)}")
    return lines


def main() -> None:
    payload = read_payload()
    project = Project(project_root(payload))
    if not project.enabled:
        return

    sync_statusline()
    added = migrate_config(project)
    merge_gitignore(project)
    config = project.config()

    out: list[str] = []
    if legacy_install(project):
        out.append(
            "[TAKSHAK] Legacy copy-installed hooks are still wired in .claude/settings.json and "
            "will double-fire alongside the plugin. Tell the user to run /takshak:init to migrate."
        )
    if added:
        out.append(f"[TAKSHAK] framework.json gained new settings with defaults: {', '.join(added)}")
    if banner := budget_banner(project):
        out.append(banner)

    branch = git(project.root, "rev-parse", "--abbrev-ref", "HEAD")
    for title, snippet in relevant_memories(memory_dir(project, payload), context_keywords(project, config, branch)):
        out.append(f"[MEMORY] {title}: {snippet}")

    out.extend(plan_lines(project))
    out.extend(checkpoint_lines(project))

    if out:
        print("\n".join(out))


if __name__ == "__main__":
    run_safely(main)
