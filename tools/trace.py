"""Takshak traceability -- requirement -> task -> test, checked in code.

    run.sh tools/trace.py show  --project DIR [--json] [--sdd PATH]
    run.sh tools/trace.py check --project DIR --mode plan|ship [--sdd PATH]   # exit 1 on gaps

`plan` runs at breakdown time (every requirement has a task, every task has a requirement);
`ship` also requires a test marked `covers: <id>` for every non-deferred requirement.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import _trace as trace  # noqa: E402
from _common import Project  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("show", "check"):
        p = sub.add_parser(name)
        p.add_argument("--project", default=".")
        p.add_argument("--sdd")
        if name == "show":
            p.add_argument("--json", action="store_true")
        else:
            p.add_argument("--mode", choices=["plan", "ship"], default="plan")
    args = parser.parse_args()
    project = Project(Path(args.project))
    report = trace.build(project, args.sdd)

    if args.command == "show":
        print(json.dumps(report, indent=2) if args.json else trace.render_markdown(report))
        return
    found = trace.problems(report, args.mode)
    for msg in found:
        print(f"[trace] {msg}")
    if not found:
        s = report["summary"]
        print("[trace] " + ("no requirement IDs to trace" if not report["has_ids"] else
                            f"{s['covered']} covered, {s['untested']} untested, {s['unplanned']} unplanned, {s['deferred']} deferred"))
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()
