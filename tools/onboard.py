"""Takshak onboarding guide -- scaffold it, then check it in code.

    run.sh tools/onboard.py scaffold --project DIR [--name NAME]      # docs/onboarding/<slug>.md from the template
    run.sh tools/onboard.py check    --project DIR [--doc PATH]       # exit 1 when the guide is not trustworthy yet

The guide is prose written by the model, but whether it is *complete and true enough to hand a
newcomer* is mechanical: every section present and filled, diagrams drawn, glossary and exercises
present, and every `file:line` citation pointing at a line that exists.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import _structure as structure  # noqa: E402
from _common import Project  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "ONBOARDING.md.template"
SECTIONS = ("Start here", "The system in one picture", "Words you will see", "Repository tour", "Follow one run end to end",
            "Data and state", "Module walkthrough", "Configuration and environment", "Run it, test it, debug it",
            "Sharp edges", "How to make common changes", "Open questions", "Check your understanding")
CITE = re.compile(r"`([\w./\\-]+\.[A-Za-z0-9]+):(\d+)(?:-(\d+))?`")
PLACEHOLDER = re.compile(r"\{\{[^}\n]*\}\}")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "project"


def guide_dir(project: Project) -> Path:
    return project.root / structure.dirs(project)["guide"]


def find_doc(project: Project, override: str | None) -> Path | None:
    if override:
        p = Path(override)
        return p if p.is_absolute() else project.root / p
    docs = sorted(p for p in guide_dir(project).glob("*.md") if p.stem.lower() not in structure.INDEX_NAMES) if guide_dir(project).is_dir() else []
    return docs[0] if len(docs) == 1 else None


def git_sha(root: Path) -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, timeout=10, check=False).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def problems(project: Project, doc: Path) -> list[str]:
    text = doc.read_text(encoding="utf-8", errors="replace")
    out: list[str] = []
    if not re.match(r"#\s*Onboarding Guide\b", text.lstrip()):
        out.append("first heading must be `# Onboarding Guide: <name>` (that is how takshak recognises the document)")
    heads = [h.lower() for h in re.findall(r"^##\s+(?:\d+\.\s*)?(.+?)\s*$", text, re.MULTILINE)]
    for sec in SECTIONS:
        if not any(h.startswith(sec.lower()) for h in heads):
            out.append(f"missing section: {sec}")
    if "```mermaid" not in text or len(re.findall(r"```mermaid", text)) < 2:
        out.append("needs at least two mermaid diagrams: the system picture and the run-through sequence")
    if not re.search(r"```mermaid\s+(?:flowchart|graph)\b", text):
        out.append("the system picture must be a mermaid flowchart")
    if not re.search(r"```mermaid\s+sequenceDiagram\b", text):
        out.append("the run-through must include a mermaid sequenceDiagram")
    left = [m.group(0) for m in PLACEHOLDER.finditer(text)]
    if left:
        out.append(f"{len(left)} unfilled placeholder(s), e.g. {left[0]!r}")
    if not re.search(r"Coverage:\s*\S*\d+\s+of\s+\d+", text):
        out.append("state the coverage in the header, e.g. `Coverage: 6 of 9 modules`, so a partial guide is not mistaken for a full one")
    if not re.search(r"Verified by running\?\s*\S", text):
        out.append("section 9 must say what was actually run and what was only read")
    glossary = re.search(r"##\s+(?:\d+\.\s*)?Words you will see(.*?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL)
    if not glossary or len([r for r in glossary.group(1).splitlines() if r.startswith("|")]) < 4:
        out.append("glossary needs at least two real terms")
    ex = re.search(r"##\s+(?:\d+\.\s*)?Check your understanding(.*?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL)
    if not ex or len(re.findall(r"^\s*\d+\.", ex.group(1), re.MULTILINE)) < 2:
        out.append("needs at least two questions or exercises at the end")
    cites = CITE.findall(text)
    if len(cites) < 8:
        out.append(f"only {len(cites)} file:line citations; a guide anyone can trust cites its claims (at least 8)")
    cache: dict[Path, int | None] = {}
    bad = []
    for path, a, b in cites:
        target = project.root / path.replace("\\", "/")
        if target not in cache:
            try:
                cache[target] = len(target.read_text(encoding="utf-8", errors="replace").splitlines()) if target.is_file() else None
            except OSError:
                cache[target] = None
        n = cache[target]
        last = int(b or a)
        if n is None:
            bad.append(f"{path}:{a} -- file not found")
        elif int(a) < 1 or last > n or last < int(a):
            bad.append(f"{path}:{a}{'-' + b if b else ''} -- file has {n} lines")
    out.extend(f"bad citation {b}" for b in sorted(set(bad))[:15])
    if len(set(bad)) > 15:
        out.append(f"... and {len(set(bad)) - 15} more bad citations")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("scaffold")
    s.add_argument("--project", default=".")
    s.add_argument("--name")
    c = sub.add_parser("check")
    c.add_argument("--project", default=".")
    c.add_argument("--doc")
    args = parser.parse_args()
    project = Project(Path(args.project))

    if args.command == "scaffold":
        name = args.name or project.config().get("project_name") or project.root.name
        target = guide_dir(project) / f"{slug(str(name))}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            print(json.dumps({"exists": target.relative_to(project.root).as_posix()}))
            return
        body = TEMPLATE.read_text(encoding="utf-8").replace("{{project name}}", str(name))
        body = body.replace("{{ISO date}}", date.today().isoformat()).replace("{{short sha}}", git_sha(project.root))
        target.write_text(body, encoding="utf-8")
        print(json.dumps({"created": target.relative_to(project.root).as_posix()}))
        return

    doc = find_doc(project, args.doc)
    if doc is None or not doc.is_file():
        print("[onboard] no onboarding guide found (or several: pass --doc); create one with `onboard.py scaffold`")
        sys.exit(1)
    found = problems(project, doc)
    for p in found:
        print(f"[onboard] {p}")
    if not found:
        print(f"[onboard] {doc.relative_to(project.root).as_posix()}: complete, citations resolve")
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()
