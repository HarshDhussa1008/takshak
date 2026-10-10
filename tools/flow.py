"""Takshak flow -- what is the next step in the pipeline?

    run.sh tools/flow.py next --project DIR [--json]
    run.sh tools/flow.py mark retro|shipped --project DIR     # called by those skills when they finish

`next` reads the design, plan, work-item, reconcile and design-change state and says which stage
the work is in, what comes next, and who must move (claude / user / none). /takshak:start follows it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import _flow as flow  # noqa: E402
from _common import Project  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("next")
    p.add_argument("--project", default=".")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("mark")
    p.add_argument("what", choices=flow.MARKS)
    p.add_argument("--project", default=".")
    args = parser.parse_args()
    project = Project(Path(args.project))
    if args.command == "next":
        info = flow.next_step(project)
        print(json.dumps(info, indent=2) if args.json else flow.line(info))
    else:
        flow.mark(project, args.what)
        print(f"marked {args.what}")


if __name__ == "__main__":
    main()
