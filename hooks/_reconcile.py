"""Documentation-drift bookkeeping, shared by the tracker hook, the Stop hook, the dashboard,
and tools/reconcile.py.

The split of labour is the same as everywhere else in takshak: *code* decides whether the
docs have drifted from the implementation (cheap, deterministic, no LLM); the
/takshak:reconcile skill does the part that needs judgement (reading the code and rewriting
the as-built Implementation Guide). A hook can nag, it cannot write prose -- so this module
only ever answers "what changed since the docs were last reconciled, and which documented
sections does that touch?".

State lives in .claude/reconcile_state.json:
    baseline_tree      snapshot of the working tree at the last reconcile (what drift is measured from)
    baseline_commit    HEAD at the last reconcile, for display
    baseline_at        ISO time of the last reconcile
    edited             {relative path: edit count} for Edit/Write calls since then
    reconciled_tasks   task ids that were already completed at the last reconcile
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from _common import Project, git, now_iso, read_json, write_json

STATE_NAME = "reconcile_state.json"
EDITED_CAP = 500
DOC_SUFFIXES = {".md", ".mdx", ".rst", ".txt", ".adoc"}
NON_SOURCE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".lock", ".map", ".pyc", ".log", ".html"}
SKIP_DIRS = {"__pycache__", "dist", "build", "node_modules", ".git", ".venv", "venv", ".tox", ".mypy_cache", ".claude"}
TEST_DIRS = {"tests", "test", "__tests__", "spec", "e2e"}
TEST_NAME = re.compile(r"(^test_|_test\.\w+$|\.test\.\w+$|\.spec\.\w+$|^conftest\.py$)")
COVERS = re.compile(r"<!--\s*covers:\s*(.*?)\s*-->", re.IGNORECASE)
HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*$")


def state_path(project: Project) -> Path:
    return project.state_dir / STATE_NAME


def load(project: Project) -> dict[str, Any]:
    return read_json(state_path(project))


def save(project: Project, state: dict[str, Any]) -> None:
    write_json(state_path(project), state)


def enabled(project: Project) -> bool:
    return bool(project.config().get("reconcile", True))


def relative(project: Project, raw: str | Path) -> str | None:
    """Project-relative POSIX path, or None when the file lives outside the project."""
    path = Path(raw)
    try:
        if path.is_absolute():
            return path.resolve().relative_to(project.root.resolve()).as_posix()
        return PurePosixPath(path.as_posix()).as_posix()
    except (ValueError, OSError):
        return None


def impl_dir(project: Project) -> str:
    return str(project.config().get("impl_doc_path") or "docs/implementation").strip("/")


def sdd_dir(project: Project) -> str:
    return str(project.config().get("sdd_path") or "docs/sdd").strip("/")


def is_source_file(project: Project, rel: str) -> bool:
    """True for files whose change can make the Implementation Guide wrong: real source,
    config, schemas, API specs. Docs, tests, state, build output and binaries do not count."""
    posix = PurePosixPath(rel)
    parts = set(posix.parts)
    if parts & SKIP_DIRS or parts & TEST_DIRS or TEST_NAME.search(posix.name):
        return False
    if posix.suffix.lower() in DOC_SUFFIXES or posix.suffix.lower() in NON_SOURCE_SUFFIXES:
        return False
    for prefix in (impl_dir(project), sdd_dir(project)):
        if rel == prefix or rel.startswith(prefix + "/"):
            return False
    return True


def record_edit(project: Project, rel: str) -> None:
    if not is_source_file(project, rel):
        return
    state = load(project)
    edited = state.setdefault("edited", {})
    edited[rel] = int(edited.get(rel, 0)) + 1
    if len(edited) > EDITED_CAP:
        for stale in sorted(edited, key=edited.get)[: len(edited) - EDITED_CAP]:  # type: ignore[arg-type]
            edited.pop(stale, None)
    save(project, state)


def impl_doc_rel(project: Project) -> str:
    """Where this project's Implementation Guide lives: the path recorded by the last
    `mark --doc` (an adopted legacy codebase has no SDD to name it after), else
    <impl_doc_path>/<sdd-slug>.md, else <impl_doc_path>/implementation.md."""
    if recorded := load(project).get("impl_doc"):
        return str(recorded)
    sdd = read_json(project.task_state).get("sdd_path")
    slug = PurePosixPath(str(sdd)).stem if sdd else "implementation"
    return f"{impl_dir(project)}/{slug or 'implementation'}.md"


def parse_covers(doc_text: str) -> dict[str, list[str]]:
    """{section heading: [paths it covers]} from `<!-- covers: a.py, pkg/ -->` markers.

    A marker belongs to the nearest heading above it. A trailing `/` means "everything
    under this directory"."""
    covers: dict[str, list[str]] = {}
    current = ""
    for line in doc_text.splitlines():
        if m := HEADING.match(line):
            current = m.group(2)
            continue
        if current and (c := COVERS.search(line)):
            paths = [p.strip() for p in c.group(1).split(",") if p.strip()]
            covers.setdefault(current, []).extend(paths)
    return covers


def path_covered(rel: str, covered: str) -> bool:
    covered = covered.strip().lstrip("./")
    if covered.endswith("/"):
        return rel.startswith(covered)
    return rel == covered or rel.startswith(covered + "/")


def _git_type(project: Project, obj: str) -> str:
    return git(project.root, "cat-file", "-t", obj)


def snapshot_tree(project: Project) -> str | None:
    """Tree hash of the working tree *as it is right now* (tracked + untracked-not-ignored,
    committed or not), via a throwaway copy of the index so the user's staging area is never
    touched. Comparing two of these answers "did the code change?" regardless of whether the
    change has been committed yet -- which is why the baseline is a tree, not a commit: a
    baseline commit would re-flag already-reconciled files the moment the task's commit lands."""
    index = git(project.root, "rev-parse", "--git-path", "index")
    if not index:
        return None
    index_path = Path(index) if Path(index).is_absolute() else project.root / index
    tmp = project.state_dir / "reconcile_index.tmp"
    try:
        project.state_dir.mkdir(parents=True, exist_ok=True)
        if index_path.is_file():
            shutil.copyfile(index_path, tmp)
        else:
            tmp.unlink(missing_ok=True)
    except OSError:
        return None
    env = {**os.environ, "GIT_INDEX_FILE": str(tmp)}
    try:
        def run(*args: str) -> str | None:
            r = subprocess.run(["git", *args], cwd=project.root, env=env, capture_output=True,
                               text=True, timeout=20, check=False, encoding="utf-8", errors="replace")
            return r.stdout.strip() if r.returncode == 0 else None

        if run("add", "-A", "--", ".") is None:
            return None
        tree = run("write-tree")
        return tree or None
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        tmp.unlink(missing_ok=True)


def changed_files(project: Project, state: dict[str, Any]) -> list[str]:
    base = state.get("baseline_tree")
    if base and _git_type(project, str(base)) == "tree":
        current = snapshot_tree(project)
        if current:
            out = git(project.root, "diff", "--name-only", "--relative", str(base), current, "--", ".", timeout=20)
            return sorted(f for f in (ln.strip() for ln in out.splitlines()) if f and is_source_file(project, f))
    # No usable baseline (first reconcile, or not a git repo): fall back to the edit tracker,
    # plus -- before the first reconcile ever -- everything modified or untracked.
    files: set[str] = set(state.get("edited") or {})
    if not state.get("baseline_at"):
        dirty = git(project.root, "ls-files", "-m", "-o", "--exclude-standard", "--", ".", timeout=15)
        files.update(ln.strip() for ln in dirty.splitlines() if ln.strip())
    return sorted(f for f in files if is_source_file(project, f))


def completed_task_ids(project: Project) -> list[str]:
    tasks = read_json(project.task_state).get("tasks") or []
    return [str(t.get("id")) for t in tasks if t.get("status") == "completed"]


def status(project: Project) -> dict[str, Any]:
    """Everything the Stop hook, the dashboard, the ship gate and the skill need, in one dict."""
    state = load(project)
    config = project.config()
    doc_rel = impl_doc_rel(project)
    doc_path = project.root / doc_rel
    doc_text = ""
    if doc_path.is_file():
        try:
            doc_text = doc_path.read_text(encoding="utf-8")
        except OSError:
            doc_text = ""

    changed = changed_files(project, state)
    covers = parse_covers(doc_text)
    stale = sorted({h for h, paths in covers.items() for f in changed if any(path_covered(f, p) for p in paths)})
    undocumented = [f for f in changed if not any(path_covered(f, p) for paths in covers.values() for p in paths)]
    unreconciled = [t for t in completed_task_ids(project) if t not in set(state.get("reconciled_tasks") or [])]

    if not doc_path.is_file():
        needs = "init"  # code exists, no as-built guide: legacy repo (adopt) or a feature before its first reconcile
    else:
        needs = "update" if changed else "none"

    threshold = int(config.get("reconcile_edit_threshold", 5) or 5)
    return {
        "enabled": enabled(project),
        "impl_doc": doc_rel,
        "impl_doc_exists": doc_path.is_file(),
        "baseline_commit": state.get("baseline_commit"),
        "has_baseline": bool(state.get("baseline_tree")),
        "baseline_at": state.get("baseline_at"),
        "changed": changed,
        "stale_sections": stale,
        "undocumented": undocumented,
        "completed_unreconciled": unreconciled,
        "threshold": threshold,
        "needs": needs,
        # "major edit completion": a task just finished, or enough files moved since the last reconcile
        "due": needs != "none" and (bool(unreconciled and changed) or len(changed) >= threshold or (needs == "init" and bool(unreconciled))),
        # a final pass only counts while nothing has moved since it
        "final": bool(state.get("final_at")) and needs == "none",
    }


def mark(project: Project, mode: str = "update", doc: str | None = None) -> dict[str, Any]:
    """Record that the docs now match the code: baseline moves to HEAD, tracked edits clear."""
    state = load(project)
    if doc:
        state["impl_doc"] = PurePosixPath(doc).as_posix()
    head = git(project.root, "rev-parse", "HEAD")
    state["baseline_commit"] = head or state.get("baseline_commit")
    if tree := snapshot_tree(project):
        state["baseline_tree"] = tree
    state["baseline_at"] = now_iso()
    state["edited"] = {}
    state["reconciled_tasks"] = completed_task_ids(project)
    if mode == "final":
        state["final_at"] = state["baseline_at"]
    else:
        state.pop("final_at", None)
    save(project, state)
    return state
