"""Takshak ADRs -- the deterministic half of recording design decisions.

    run.sh tools/adr.py new --project DIR --title T [--requirements R1,R2]   # next number, from the template
    run.sh tools/adr.py accept|reject|deprecate N --project DIR
    run.sh tools/adr.py supersede N --by M --project DIR                     # N becomes "superseded by ADR-M"
    run.sh tools/adr.py list --project DIR [--json]
    run.sh tools/adr.py check --project DIR                                  # exit 1 on structural problems

The skills write the Context / Options / Decision / Consequences text; this tool owns numbering,
the status field, supersession links and the "does every decision list real alternatives?" check.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import _adr as adr  # noqa: E402
import _trace as trace  # noqa: E402
from _common import Project  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "ADR.md.template"


def cmd_new(args: argparse.Namespace) -> int:
    project = Project(Path(args.project))
    reqs = [r.strip() for r in (args.requirements or "").split(",") if r.strip()]
    path = adr.create(project, args.title, TEMPLATE.read_text(encoding="utf-8"), reqs, today=date.today().isoformat())
    print(path.relative_to(project.root).as_posix())
    return 0


def cmd_status(args: argparse.Namespace, status: str) -> int:
    project = Project(Path(args.project))
    path = adr.find(project, args.number)
    if path is None:
        print(f"no ADR-{args.number:04d}", file=sys.stderr)
        return 1
    adr.set_status(path, status)
    print(f"ADR-{args.number:04d}: {status}")
    return 0


def cmd_supersede(args: argparse.Namespace) -> int:
    project = Project(Path(args.project))
    old, new = adr.find(project, args.number), adr.find(project, args.by)
    if old is None or new is None or args.number == args.by:
        print("both ADRs must exist and differ", file=sys.stderr)
        return 1
    adr.set_status(old, f"superseded by ADR-{args.by:04d}")
    current = adr.parse(new)["supersedes"]
    adr.set_supersedes(new, sorted({*current, args.number}))
    print(f"ADR-{args.number:04d} superseded by ADR-{args.by:04d}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    rows = [{k: v for k, v in a.items() if k != "text"} for a in adr.load_all(Project(Path(args.project)))]
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print(f"ADR-{r['number']:04d}  {r['status']:<24} {r['title']}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    project = Project(Path(args.project))
    report = trace.build(project)
    known = {r["id"] for r in report["requirements"]} if report["has_ids"] else None
    problems = adr.check(project, known)
    for p in problems:
        print(f"[adr] {p}")
    if not problems:
        print(f"[adr] {len(adr.load_all(project))} record(s), all sound")
    return 1 if problems else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func) -> argparse.ArgumentParser:  # type: ignore[no-untyped-def]
        p = sub.add_parser(name)
        p.add_argument("--project", default=".")
        p.set_defaults(func=func)
        return p

    p = add("new", cmd_new)
    p.add_argument("--title", required=True)
    p.add_argument("--requirements")
    for name, status in (("accept", "accepted"), ("reject", "rejected"), ("deprecate", "deprecated")):
        p = add(name, lambda a, s=status: cmd_status(a, s))  # type: ignore[misc]
        p.add_argument("number", type=lambda v: int(re.sub(r"\D", "", v)))
    p = add("supersede", cmd_supersede)
    p.add_argument("number", type=lambda v: int(re.sub(r"\D", "", v)))
    p.add_argument("--by", required=True, type=lambda v: int(re.sub(r"\D", "", v)))
    p = add("list", cmd_list)
    p.add_argument("--json", action="store_true")
    add("check", cmd_check)
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
