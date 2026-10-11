"""Takshak structure -- one canonical repository/documentation layout, plus architecture fitness rules.

    run.sh tools/structure.py scaffold --project DIR             # docs dirs, ARCHITECTURE.md, CONVENTIONS.md, starter rules
    run.sh tools/structure.py check    --project DIR [--json]    # documents in the wrong place / wrong name; exit 1 on errors
    run.sh tools/structure.py fitness  --project DIR [--json] [--all]     # architecture rules from .claude/fitness.json; exit 1 on violations
    run.sh tools/structure.py fitness  --project DIR --baseline  # adopt the rules on an existing codebase: tolerate today's violations, fail new ones
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import _fitness as fitness  # noqa: E402
import _structure as structure  # noqa: E402
from _common import Project  # noqa: E402
from _reconcile import SKIP_DIRS  # noqa: E402

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def layout_table(project: Project) -> str:
    rows = ["| Path | Purpose |", "|------|---------|"]
    for child in sorted(project.root.iterdir()):
        if child.name.startswith(".") or child.name in SKIP_DIRS:
            continue
        rows.append(f"| `{child.name}{'/' if child.is_dir() else ''}` | {{{{what lives here}}}} |")
    return "\n".join(rows)


def cmd_scaffold(args: argparse.Namespace) -> int:
    project = Project(Path(args.project))
    created = []
    for rel in structure.dirs(project).values():
        d = project.root / rel
        if not d.exists():
            d.mkdir(parents=True)
            (d / ".gitkeep").write_text("", encoding="utf-8")
            created.append(rel + "/")
    name = project.config().get("project_name") or project.root.name
    for fname, template in (("ARCHITECTURE.md", "ARCHITECTURE.md.template"), ("CONVENTIONS.md", "CONVENTIONS.md.template")):
        path = project.root / fname
        if not path.exists():
            body = (TEMPLATES / template).read_text(encoding="utf-8").replace("{{project name}}", str(name))
            path.write_text(body.replace("{{layout}}", layout_table(project)), encoding="utf-8")
            created.append(fname)
    if not fitness.rules_path(project).exists():
        starter = {"rules": [
            {"id": "no-cycles", "type": "no-cycles", "scope": "", "description": "no import cycles between modules"},
            {"id": "file-size", "type": "max-lines", "scope": "", "max": 800, "description": "split files before they become junk drawers"},
        ], "baseline": []}
        fitness.rules_path(project).write_text(json.dumps(starter, indent=2) + "\n", encoding="utf-8")
        created.append(".claude/fitness.json")
    print(json.dumps({"created": created}, indent=2))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    project = Project(Path(args.project))
    problems = structure.check(project)
    errors = [p for p in problems if p["severity"] == "error"]
    if args.json:
        print(json.dumps({"problems": problems, "errors": len(errors)}, indent=2))
    else:
        for p in problems:
            print(f"[{p['severity']}] {p['rule']}: {p['path']} -- {p['message']}")
        if not problems:
            print("[structure] layout is consistent")
    return 1 if errors else 0


def cmd_fitness(args: argparse.Namespace) -> int:
    project = Project(Path(args.project))
    if args.baseline:
        print(f"baseline recorded: {fitness.baseline_now(project)} existing violation(s) tolerated; new ones will fail")
        return 0
    violations = fitness.check(project, include_baseline=args.all)
    if args.json:
        print(json.dumps({"violations": violations}, indent=2))
    else:
        for v in violations:
            print(f"[fitness] {v['rule']}: {v['file']} -- {v['detail']}" + (f"  ({v['description']})" if v["description"] else ""))
        if not violations:
            print(f"[fitness] {len(fitness.load_rules(project)['rules'])} rule(s), no new violations")
    return 1 if violations else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("scaffold", cmd_scaffold), ("check", cmd_check), ("fitness", cmd_fitness)):
        p = sub.add_parser(name)
        p.add_argument("--project", default=".")
        p.set_defaults(func=func)
        if name in ("check", "fitness"):
            p.add_argument("--json", action="store_true")
        if name == "fitness":
            p.add_argument("--baseline", action="store_true")
            p.add_argument("--all", action="store_true", help="include baselined violations")
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
