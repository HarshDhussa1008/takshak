"""Repository and documentation structure: one canonical layout, enforced.

Documents drift into inconsistent places and shapes faster than code does: an ADR in `notes/`,
an SDD called `Design_v2_FINAL.md`, an LLD with no design behind it. A new engineer then has to
learn each feature's private filing system. This module defines the one layout takshak writes to,
recognises documents by what they *are* (their heading), and reports what is out of place:

    docs/adr/NNNN-title.md         decisions            (# ADR-NNNN: ...)
    docs/sdd/<slug>.md             high-level design    (# SDD: ...)
    docs/lld/<slug>.md             low-level design     (# LLD: ...)
    docs/design-notes/<slug>.md    discussion record    (# Design notes: ...)
    docs/implementation/<slug>.md  as-built guide       (# Implementation Guide: ...)
    docs/onboarding/<slug>.md      newcomer walkthrough (# Onboarding Guide: ...)
    docs/reviews/<slug>.html       reviewer packs
    ARCHITECTURE.md, CONVENTIONS.md at the repository root
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

from _common import Project

HEADING_KINDS = (
    (re.compile(r"^#\s*ADR-\d+", re.IGNORECASE), "adr"),
    (re.compile(r"^#\s*SDD\b", re.IGNORECASE), "sdd"),
    (re.compile(r"^#\s*LLD\b", re.IGNORECASE), "lld"),
    (re.compile(r"^#\s*Design notes\b", re.IGNORECASE), "notes"),
    (re.compile(r"^#\s*Implementation Guide\b", re.IGNORECASE), "impl"),
    (re.compile(r"^#\s*Onboarding Guide\b", re.IGNORECASE), "guide"),
)
KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ADR_NAME = re.compile(r"^\d{4}-[a-z0-9]+(?:-[a-z0-9]+)*$")
ROOT_ALLOWED = {
    "readme", "changelog", "license", "licence", "contributing", "architecture", "conventions", "security",
    "code_of_conduct", "agents", "claude", "authors", "notice", "roadmap", "support", "setup", "install", "history", "todo",
}
INDEX_NAMES = {"readme", "index"}


def dirs(project: Project) -> dict[str, str]:
    cfg = project.config()

    def d(key: str, default: str) -> str:
        return str(cfg.get(key) or default).strip("/")

    return {
        "adr": d("adr_path", "docs/adr"), "sdd": d("sdd_path", "docs/sdd"), "lld": d("lld_path", "docs/lld"),
        "notes": d("notes_path", "docs/design-notes"), "impl": d("impl_doc_path", "docs/implementation"),
        "reviews": d("review_path", "docs/reviews"), "guide": d("onboarding_path", "docs/onboarding"),
    }


def kind_of(text: str) -> str | None:
    for line in text.splitlines()[:8]:
        for rx, kind in HEADING_KINDS:
            if rx.match(line.strip()):
                return kind
    return None


def location_problem(project: Project, rel: str, text: str) -> dict[str, str] | None:
    """Is this document where its kind belongs? None when fine or when it is not a takshak document."""
    kind = kind_of(text)
    if kind is None:
        return None
    where = dirs(project)[kind]
    parent = PurePosixPath(rel).parent.as_posix()
    if parent != where and not parent.startswith(where + "/"):
        hint = {"adr": "create it with `tools/adr.py new` so it gets the next number",
                "sdd": "scaffold it with `tools/design.py scaffold`",
                "lld": "write it with /takshak:lld", "notes": "scaffold it with `tools/design.py scaffold`",
                "impl": "write it with /takshak:reconcile", "guide": "write it with /takshak:reconcile"}[kind]
        return {"rule": "misplaced-doc", "path": rel, "message": f"this is a { {'impl': 'implementation guide', 'guide': 'onboarding guide'}.get(kind, kind.upper()) } but it lives in {parent or '/'}; "
                f"it belongs in {where}/ ({hint})", "severity": "error"}
    return None


def check(project: Project) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []
    root = project.root
    where = dirs(project)
    canonical = set(where.values())
    extra = {str(x).strip("/") for x in project.config().get("layout_extra_dirs") or []}

    docs = root / "docs"
    if docs.is_dir():
        for child in sorted(docs.iterdir()):
            if child.is_dir():
                rel = child.relative_to(root).as_posix()
                if rel not in canonical and rel not in extra and rel not in {"docs/runbooks", "docs/api", "docs/assets", "docs/images", "docs/architecture"}:
                    problems.append({"rule": "unknown-docs-dir", "path": rel + "/", "severity": "warn",
                                     "message": "not a takshak documentation directory; add it to `layout_extra_dirs` in framework.json if it is intentional"})
        for md in sorted(docs.rglob("*.md")):
            rel = md.relative_to(root).as_posix()
            try:
                head = md.read_text(encoding="utf-8", errors="replace")[:600]
            except OSError:
                continue
            if bad := location_problem(project, rel, head):
                problems.append(bad)

    for kind, rel_dir in where.items():
        d = root / rel_dir
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if not f.is_file() or f.name.startswith(".") or f.stem.lower() in INDEX_NAMES:
                continue
            stem = f.stem.replace(".freeze", "")
            ok = ADR_NAME.match(stem) if kind == "adr" else KEBAB.match(stem)
            if not ok:
                want = "NNNN-kebab-title" if kind == "adr" else "kebab-case"
                problems.append({"rule": "naming", "path": f.relative_to(root).as_posix(), "severity": "error",
                                 "message": f"file name should be {want}.md (lowercase, hyphens, no spaces/underscores/versions like _v2_FINAL)"})

    sdd_slugs = {p.stem for p in (root / where["sdd"]).glob("*.md")} if (root / where["sdd"]).is_dir() else set()
    for kind in ("lld", "notes", "impl"):
        d = root / where[kind]
        if not d.is_dir() or not sdd_slugs:
            continue
        for f in sorted(d.glob("*.md")):
            if f.stem.lower() not in INDEX_NAMES and f.stem not in sdd_slugs and kind != "impl":
                problems.append({"rule": "orphan-doc", "path": f.relative_to(root).as_posix(), "severity": "warn",
                                 "message": f"no matching SDD named {f.stem}.md in {where['sdd']}/ -- a {kind} document should share its design's slug"})

    for name in ("ARCHITECTURE.md", "CONVENTIONS.md"):
        if not (root / name).is_file():
            problems.append({"rule": "missing-root-doc", "path": name, "severity": "warn",
                             "message": "missing; `tools/structure.py scaffold` creates it so every engineer (and Claude) starts from the same map"})

    for f in sorted(root.glob("*.md")):
        if f.stem.lower() not in ROOT_ALLOWED:
            problems.append({"rule": "stray-root-doc", "path": f.name, "severity": "warn",
                             "message": "documents belong under docs/ (adr, sdd, lld, design-notes, implementation, reviews) -- the repository root is for README, CHANGELOG, ARCHITECTURE, CONVENTIONS and similar"})
    return problems


def write_violation(project: Project, path: Path, tool_name: str, tool_input: dict[str, Any]) -> str | None:
    """PreToolUse check for a *new* document being written to the wrong place. Narrow on purpose:
    only Write, only a markdown file, only when its own heading says what it is."""
    if tool_name != "Write" or path.suffix.lower() != ".md":
        return None
    try:
        rel = path.resolve().relative_to(project.root.resolve()).as_posix()
    except (ValueError, OSError):
        return None
    if path.exists():
        return None  # editing an existing file is not (re)filing it
    problem = location_problem(project, rel, str(tool_input.get("content", "")))
    return problem["message"] if problem else None
