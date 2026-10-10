"""Architecture Decision Records: numbering, status changes, and the structural check.

An ADR is where a design *discussion* is recorded so it is never held in anyone's head or lost in
a chat: the context, the options that were actually weighed, the decision and its price. Code
owns the bookkeeping (numbering, supersession links, "does this record have alternatives?");
the /takshak:design and /takshak:redesign skills own the thinking.

    docs/adr/0007-queue-over-cron.md
        # ADR-0007: Queue over cron
        - Status: proposed | accepted | rejected | deprecated | superseded by ADR-0009
        - Requirements: R1, R3
        - Supersedes: ADR-0003
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from _common import Project

FILE_RE = re.compile(r"^(\d{4})-([a-z0-9]+(?:-[a-z0-9]+)*)\.md$")
TITLE_RE = re.compile(r"^#\s*ADR-(\d{4}):\s*(.+?)\s*$", re.MULTILINE)
STATUS_RE = re.compile(r"^-\s*Status:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
REQS_RE = re.compile(r"^-\s*Requirements:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
SUPERSEDES_RE = re.compile(r"^-\s*Supersedes:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
OPTION_RE = re.compile(r"^###\s+Option\b", re.MULTILINE | re.IGNORECASE)
PLACEHOLDER_RE = re.compile(r"<[^>\n]{2,}>|\bTBD\b|\bTODO\b|\?\?\?")
STATUSES = ("proposed", "accepted", "rejected", "deprecated")


def adr_dir(project: Project) -> Path:
    return project.root / str(project.config().get("adr_path") or "docs/adr").strip("/")


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60].strip("-") or "decision"


def section(text: str, heading: str) -> str:
    m = re.search(rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def parse(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    fm = FILE_RE.match(path.name)
    title = TITLE_RE.search(text)
    status = STATUS_RE.search(text)
    reqs = REQS_RE.search(text)
    sup = SUPERSEDES_RE.search(text)
    return {
        "file": path.name,
        "number": int(fm.group(1)) if fm else None,
        "title": title.group(2) if title else path.stem,
        "title_number": int(title.group(1)) if title else None,
        "status": status.group(1).strip() if status else "",
        "requirements": [r.strip() for r in re.split(r"[,\s]+", reqs.group(1)) if re.match(r"^[A-Z]{1,3}\d+$", r.strip())] if reqs else [],
        "supersedes": [int(n) for n in re.findall(r"ADR-(\d{4})", sup.group(1))] if sup else [],
        "options": len(OPTION_RE.findall(text)),
        "text": text,
    }


def load_all(project: Project) -> list[dict[str, Any]]:
    d = adr_dir(project)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        if p.name.lower() in ("readme.md", "index.md"):
            continue
        try:
            out.append(parse(p))
        except OSError:
            continue
    return out


def next_number(project: Project) -> int:
    nums = [a["number"] for a in load_all(project) if a["number"]]
    return (max(nums) if nums else 0) + 1


def create(project: Project, title: str, template: str, requirements: list[str] | None = None,
           status: str = "proposed", today: str = "") -> Path:
    number = next_number(project)
    d = adr_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{number:04d}-{slugify(title)}.md"
    body = template.replace("<NNNN>", f"{number:04d}").replace("<decision title>", title.strip())
    body = body.replace("- Status: proposed", f"- Status: {status}")
    if today:
        body = body.replace("<ISO date>", today)
    body = body.replace('<R1, R3 — the requirement IDs this decision serves, or "-">', ", ".join(requirements) if requirements else "-")
    path.write_text(body, encoding="utf-8")
    return path


def find(project: Project, number: int) -> Path | None:
    for a in load_all(project):
        if a["number"] == number:
            return adr_dir(project) / a["file"]
    return None


def set_status(path: Path, status: str) -> None:
    text = path.read_text(encoding="utf-8")
    if STATUS_RE.search(text):
        text = STATUS_RE.sub(f"- Status: {status}", text, count=1)
    else:
        text = TITLE_RE.sub(lambda m: f"{m.group(0)}\n\n- Status: {status}", text, count=1)
    path.write_text(text, encoding="utf-8")


def set_supersedes(path: Path, numbers: list[int]) -> None:
    text = path.read_text(encoding="utf-8")
    value = ", ".join(f"ADR-{n:04d}" for n in numbers) or "-"
    if SUPERSEDES_RE.search(text):
        text = SUPERSEDES_RE.sub(f"- Supersedes: {value}", text, count=1)
    else:
        text = STATUS_RE.sub(lambda m: f"{m.group(0)}\n- Supersedes: {value}", text, count=1)
    path.write_text(text, encoding="utf-8")


def is_live(adr: dict[str, Any]) -> bool:
    return adr["status"].lower().startswith("accepted")


def check(project: Project, known_requirements: set[str] | None = None) -> list[str]:
    """Structural problems across all ADRs; an empty list means the decision log is sound."""
    adrs = load_all(project)
    problems: list[str] = []
    seen: dict[int, str] = {}
    numbers = sorted(a["number"] for a in adrs if a["number"])
    for a in adrs:
        name = a["file"]
        if a["number"] is None:
            problems.append(f"{name}: file name must be NNNN-kebab-title.md")
            continue
        if a["number"] in seen:
            problems.append(f"{name}: duplicate ADR number {a['number']:04d} (also {seen[a['number']]})")
        seen[a["number"]] = name
        if a["title_number"] != a["number"]:
            problems.append(f"{name}: heading says ADR-{a['title_number'] or '????'} but the file is number {a['number']:04d}")
        status = a["status"].lower()
        if not (status in STATUSES or status.startswith("superseded by adr-")):
            problems.append(f"{name}: unknown status '{a['status']}'")
        text = a["text"]
        for heading in ("Context", "Options considered", "Decision", "Consequences"):
            body = section(text, heading)
            if not body or (PLACEHOLDER_RE.search(body) and len(PLACEHOLDER_RE.sub("", body).strip()) < 20):
                problems.append(f"{name}: '## {heading}' is empty or still a template placeholder")
        if a["options"] < 2 and not status.startswith("rejected"):
            problems.append(f"{name}: needs at least two '### Option' alternatives (found {a['options']}) -- a decision with one option is an assumption")
        for target in a["supersedes"]:
            old = next((x for x in adrs if x["number"] == target), None)
            if old is None:
                problems.append(f"{name}: supersedes ADR-{target:04d}, which does not exist")
            elif not old["status"].lower().startswith(f"superseded by adr-{a['number']:04d}"):
                problems.append(f"{old['file']}: should be 'superseded by ADR-{a['number']:04d}' (superseded in {name})")
        if known_requirements is not None:
            for r in a["requirements"]:
                if r not in known_requirements:
                    problems.append(f"{name}: references unknown requirement {r}")
    if numbers and numbers != list(range(1, len(numbers) + 1)) and len(set(numbers)) == len(numbers):
        missing = sorted(set(range(1, max(numbers) + 1)) - set(numbers))
        problems.append(f"gap in ADR numbering: missing {', '.join(f'{n:04d}' for n in missing)} (numbers are never reused or removed -- mark old ones deprecated)")
    return problems
