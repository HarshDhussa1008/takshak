"""Requirement traceability: requirement -> task -> test, derived from the artifacts, not maintained by hand.

"Oh, we missed this" is almost always a requirement that never became a task, or a task that never
got a test. Both are mechanical questions once the artifacts carry stable IDs:

  SDD   `## Requirements` table, one row per requirement:  | R1 | text | acceptance criterion | Must |
  tasks task_state.json tasks carry  "requirements": ["R1", "R3"]
  tests any test file carries a marker:  `# covers: R1, R3`  (or `// covers: R1`, `@covers("R1")`)

`build` joins them and reports the gaps. The skills write the content; this module only answers
"is every requirement planned, is every task justified, is every requirement tested?" -- cheaply
enough to run from a hook, the ship gate and the review pack.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from _common import Project, read_json
from _reconcile import SKIP_DIRS, TEST_DIRS, TEST_NAME

ID_RE = re.compile(r"^[A-Z]{1,3}\d+$")
COVERS_RE = re.compile(r"""\bcovers\s*[:=(]\s*["']?([A-Z]{1,3}\d+(?:\s*[,;/\s]\s*["']?[A-Z]{1,3}\d+)*)""")
DEFERRED_RE = re.compile(r"\b(won'?t|later|deferred|out of scope|could)\b", re.IGNORECASE)
MAX_TEST_FILES = 3000
MAX_BYTES = 200_000


def section_body(text: str, heading: str) -> str:
    m = re.search(rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else ""


def split_row(line: str) -> list[str]:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return cells


def parse_requirements(sdd_text: str) -> list[dict[str, str]]:
    """Rows of the SDD's `## Requirements` table. Columns are found by header name, so the
    table may add or reorder columns (Type, Owner, ...) without breaking this."""
    body = section_body(sdd_text, "Requirements")
    rows = [ln for ln in body.splitlines() if ln.strip().startswith("|")]
    if len(rows) < 2:
        return []
    header = [h.lower() for h in split_row(rows[0])]

    def col(*names: str) -> int | None:
        for i, h in enumerate(header):
            if any(n in h for n in names):
                return i
        return None

    c_id, c_text = col("id"), col("requirement", "description", "statement")
    c_ac, c_pri, c_kind = col("accept", "criteri"), col("priority", "must"), col("type", "kind")
    if c_id is None:
        c_id = 0
    if c_text is None:
        c_text = 1
    out = []
    for ln in rows[1:]:
        cells = split_row(ln)
        if len(cells) <= max(c_id, c_text) or set("".join(cells)) <= set("-: "):
            continue
        rid = cells[c_id].strip("`*_ ")
        if not ID_RE.match(rid):
            continue
        out.append({
            "id": rid,
            "text": cells[c_text],
            "acceptance": cells[c_ac] if c_ac is not None and c_ac < len(cells) else "",
            "priority": cells[c_pri] if c_pri is not None and c_pri < len(cells) else "",
            "kind": cells[c_kind] if c_kind is not None and c_kind < len(cells) else "",
        })
    return out


def task_requirements(state: dict[str, Any]) -> dict[str, list[str]]:
    return {str(t.get("id")): [str(r) for r in (t.get("requirements") or [])] for t in state.get("tasks") or []}


def is_test_file(rel: str) -> bool:
    parts = Path(rel).parts
    return bool(set(parts) & TEST_DIRS) or bool(TEST_NAME.search(Path(rel).name))


def test_markers(project: Project) -> dict[str, list[str]]:
    """{requirement id: ['tests/test_x.py:12', ...]} from `covers:` markers in test files."""
    found: dict[str, list[str]] = {}
    count = 0
    root = project.root
    for path in root.rglob("*"):
        if count >= MAX_TEST_FILES:
            break
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if set(Path(rel).parts) & SKIP_DIRS or not path.is_file() or not is_test_file(rel):
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        count += 1
        for n, line in enumerate(lines, 1):
            for m in COVERS_RE.finditer(line):
                for rid in re.findall(r"[A-Z]{1,3}\d+", m.group(1)):
                    found.setdefault(rid, []).append(f"{rel}:{n}")
    return found


def sdd_file(project: Project, override: str | None = None) -> Path | None:
    rel = override or read_json(project.task_state).get("sdd_path")
    if rel:
        p = project.root / str(rel)
        if p.is_file():
            return p
    sdd_dir = project.root / str(project.config().get("sdd_path") or "docs/sdd")
    docs = sorted(sdd_dir.glob("*.md")) if sdd_dir.is_dir() else []
    return docs[0] if len(docs) == 1 else None


def build(project: Project, sdd_override: str | None = None) -> dict[str, Any]:
    sdd = sdd_file(project, sdd_override)
    text = sdd.read_text(encoding="utf-8", errors="replace") if sdd else ""
    reqs = parse_requirements(text)
    state = read_json(project.task_state)
    by_task = task_requirements(state)
    markers = test_markers(project)
    known = {r["id"] for r in reqs}

    rows = []
    for r in reqs:
        tasks = sorted(t for t, ids in by_task.items() if r["id"] in ids)
        tests = markers.get(r["id"], [])
        deferred = bool(DEFERRED_RE.search(r["priority"]))
        status = "deferred" if deferred else "unplanned" if not tasks else "untested" if not tests else "covered"
        rows.append({**r, "tasks": tasks, "tests": tests, "status": status})

    orphan_tasks = [t for t, ids in by_task.items() if not ids]
    unknown = sorted({(t, i) for t, ids in by_task.items() for i in ids if i not in known} |
                     {(m, i) for i, locs in markers.items() if i not in known for m in locs[:1]})
    return {
        "sdd": sdd.relative_to(project.root).as_posix() if sdd else None,
        "has_ids": bool(reqs),
        "requirements": rows,
        "orphan_tasks": orphan_tasks if reqs else [],
        "unknown_refs": [{"from": a, "ref": b} for a, b in unknown] if reqs else [],
        "summary": {s: sum(1 for r in rows if r["status"] == s) for s in ("covered", "untested", "unplanned", "deferred")},
    }


def problems(report: dict[str, Any], mode: str) -> list[str]:
    """`plan`: before implementation -- every requirement planned, every task justified.
    `ship`: additionally every requirement must be exercised by a test."""
    if not report["has_ids"]:
        return []  # legacy SDD without requirement IDs: nothing to trace (design now writes them)
    out = []
    for r in report["requirements"]:
        if r["status"] == "unplanned":
            out.append(f"{r['id']} has no task: '{r['text'][:60]}' -- nothing will build it")
        elif r["status"] == "untested" and mode == "ship":
            out.append(f"{r['id']} has no test marked `covers: {r['id']}`: '{r['text'][:60]}'")
    for t in report["orphan_tasks"]:
        out.append(f"task {t} traces to no requirement -- scope creep, or a requirement the SDD is missing")
    for u in report["unknown_refs"]:
        out.append(f"{u['from']} references {u['ref']}, which is not in the SDD's Requirements table")
    return out


def render_markdown(report: dict[str, Any]) -> str:
    if not report["has_ids"]:
        return "_No requirement IDs in the SDD (`## Requirements` table) -- nothing to trace._\n"
    lines = ["| Req | Requirement | Tasks | Tests | Status |", "|-----|-------------|-------|-------|--------|"]
    for r in report["requirements"]:
        tests = ", ".join(r["tests"][:3]) + (f" (+{len(r['tests']) - 3})" if len(r["tests"]) > 3 else "")
        lines.append(f"| {r['id']} | {r['text'][:70]} | {', '.join(r['tasks']) or '-'} | {tests or '-'} | {r['status']} |")
    return "\n".join(lines) + "\n"
