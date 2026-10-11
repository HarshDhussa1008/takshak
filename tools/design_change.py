"""Takshak design changes -- the deterministic half of /takshak:redesign.

    run.sh tools/design_change.py open    --project DIR --title T --trigger WHAT_WAS_FOUND [--kind K] [--task ID]
    run.sh tools/design_change.py propose DC-1 --project DIR [--adr 0007] [--tasks 3,4] [--sections "API Contracts"]
    run.sh tools/design_change.py apply   DC-1 --project DIR        # only after the user approved
    run.sh tools/design_change.py reject  DC-1 --project DIR
    run.sh tools/design_change.py status  --project DIR [--json]

There is deliberately no `approve` command. Approval is the user's, captured by the
UserPromptSubmit hook from their own message; the PreToolUse gate denies any attempt by Claude
to write the approval itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import _design_change as dc  # noqa: E402
from _common import Project, record_metric  # noqa: E402


def emit(obj: object) -> None:
    print(json.dumps(obj, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name)
        p.add_argument("--project", default=".")
        return p

    p = add("open")
    p.add_argument("--title", required=True)
    p.add_argument("--trigger", required=True)
    p.add_argument("--kind", default="flaw", choices=dc.KINDS)
    p.add_argument("--task")
    p = add("propose")
    p.add_argument("id")
    p.add_argument("--adr")
    p.add_argument("--tasks", default="")
    p.add_argument("--sections", default="")
    for name in ("apply", "reject"):
        add(name).add_argument("id")
    p = add("status")
    p.add_argument("--json", action="store_true")

    args = parser.parse_args()
    project = Project(Path(args.project))

    item: dict[str, Any] | None
    if args.command == "open":
        item = dc.open_change(project, args.title, args.trigger, args.kind, args.task)
        record_metric(project, "design_change_open", id=item["id"], kind=item["kind"])
        emit(item)
        return
    if args.command == "propose":
        item = dc.propose(project, args.id, args.adr, [t for t in args.tasks.split(",") if t],
                          [s.strip() for s in args.sections.split(",") if s.strip()])
    elif args.command == "apply":
        item = dc.apply(project, args.id)
        if item is None:
            print(f"{args.id} is not approved: only the user's own 'approved' message can approve it", file=sys.stderr)
            sys.exit(1)
        record_metric(project, "design_change_applied", id=item["id"], kind=item["kind"])
    elif args.command == "reject":
        item = dc.reject(project, args.id)
    else:
        data = dc.load(project)
        open_items = [i for i in data["items"] if i["status"] in ("open", "proposed", "approved")]
        if args.json:
            emit({"blocking": dc.blocking(project), "open": open_items})
        else:
            for i in open_items:
                print(f"{i['id']} [{i['status']}] {i['title']}")
            if not open_items:
                print("no open design changes")
        return
    if item is None:
        print(f"cannot update {args.id}", file=sys.stderr)
        sys.exit(1)
    emit(item)


if __name__ == "__main__":
    main()
