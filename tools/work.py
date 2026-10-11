"""Takshak work items -- the deterministic half of /takshak:work.

    run.sh tools/work.py status      --project DIR [--json]
    run.sh tools/work.py classify    --project DIR "<request text>"
    run.sh tools/work.py ticket-plan --project DIR --lane LANE [--files N] [--contract] [--adr]
                                     [--tasks N] [--deliverables N] [--parent KEY]
    run.sh tools/work.py open        --project DIR --title T --lane LANE [--jira KEY] [--jira-type NAME]
                                     [--parent KEY] [--signals-json JSON]
    run.sh tools/work.py link  ID KEY | defer ID --reason R | switch ID | done [ID] | list
    run.sh tools/work.py profile show|set|confirm|reset --project DIR ...

Every change to the codebase belongs to a work item, and with Jira on, to a ticket. This tool
holds the part that is arithmetic and bookkeeping: the work-item ledger, weighing a piece of
work into Epic / Story / Task (or Bug) / Subtask and mapping that onto the issue types the
project really has, and the remembered Jira profile (confirmed project key, type mapping,
auto-mode). The skill does the rest: reading the Jira project, asking the human to confirm,
and calling the Jira MCP to create the ticket.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import _work as work  # noqa: E402
from _common import Project, git, now_iso, read_json, record_metric, write_json  # noqa: E402


def out(data: Any) -> None:
    print(json.dumps(data, indent=2))


def signals_from(args: argparse.Namespace) -> dict[str, Any]:
    sig: dict[str, Any] = {}
    if getattr(args, "signals_json", None):
        sig.update(json.loads(args.signals_json))
    for key in ("files", "tasks", "deliverables"):
        if getattr(args, key, None) is not None:
            sig[key] = getattr(args, key)
    for key in ("contract", "adr"):
        if getattr(args, key, False):
            sig[key] = True
    return sig


def cmd_status(args: argparse.Namespace) -> int:
    project = Project(Path(args.project))
    data = work.load(project)
    active = work.active_item(project)
    info = {
        "gate": work.gate_enabled(project),
        "jira_integration": work.jira_on(project),
        "profile_mode": work.profile_mode(project),
        "active": active,
        "ticket_state": work.ticket_state(project, active) if active else None,
        "plan_covers_edits": work.plan_covers_edits(project),
        "open": [i for i in data["items"] if i.get("status") == "open"],
    }
    if args.json:
        out(info)
    elif active:
        print(f"{active['id']} [{active['lane']}] {active['title']} -- ticket: {active.get('jira_key') or info['ticket_state']}")
    else:
        print("no active work item" + (" (covered by the approved plan)" if info["plan_covers_edits"] else ""))
    return 0


def cmd_classify(args: argparse.Namespace) -> int:
    out(work.classify(" ".join(args.text)))
    return 0


def find_parent(project: Project, profile: dict[str, Any]) -> str | None:
    """A ticket a subtask could hang under: the active work item's (unless that is itself a
    subtask), the ticket named in the branch, or the profile's configured default parent."""
    item = work.active_item(project)
    sub_type = (profile.get("mapping") or {}).get("subtask")
    if item and item.get("jira_key") and item.get("jira_type") != sub_type:
        return str(item["jira_key"])
    branch = git(project.root, "rev-parse", "--abbrev-ref", "HEAD")
    if m := re.search(r"[A-Z][A-Z0-9]+-\d+", branch.upper()):
        return m.group(0)
    return profile.get("default_parent")


def cmd_ticket_plan(args: argparse.Namespace) -> int:
    project = Project(Path(args.project))
    profile = work.load_profile(project)
    parent = args.parent or find_parent(project, profile)
    decision = work.decide(args.lane, signals_from(args), profile, parent)
    mode = work.profile_mode(project)
    decision["profile_mode"] = mode
    decision["ask_user"] = mode != "auto"  # confirm until the user has handed over to auto-mode
    decision["project_key"] = profile.get("project_key") or project.config().get("jira_project_key")
    out(decision)
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    project = Project(Path(args.project))
    sizing = signals_from(args)
    item = work.open_item(project, args.title, args.lane, jira_key=args.jira, jira_type=args.jira_type,
                          parent=args.parent, sizing=sizing)
    record_metric(project, "work_open", id=item["id"], lane=item["lane"], jira=item.get("jira_key"), jira_type=item.get("jira_type"))
    out({**item, "ticket_state": work.ticket_state(project, item)})
    return 0


def cmd_link(args: argparse.Namespace) -> int:
    project = Project(Path(args.project))
    item = work.update_item(project, args.id, jira_key=args.key, jira_deferred=None)
    if item is None:
        print(f"no such work item: {args.id}", file=sys.stderr)
        return 1
    out(item)
    return 0


def cmd_defer(args: argparse.Namespace) -> int:
    project = Project(Path(args.project))
    item = work.update_item(project, args.id, jira_deferred=args.reason or "jira unavailable")
    if item is None:
        print(f"no such work item: {args.id}", file=sys.stderr)
        return 1
    out(item)
    return 0


def cmd_switch(args: argparse.Namespace) -> int:
    item = work.switch(Project(Path(args.project)), args.id)
    if item is None:
        print(f"no open work item: {args.id}", file=sys.stderr)
        return 1
    out(item)
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    project = Project(Path(args.project))
    item = work.close(project, args.id)
    if item is None:
        print("no work item to close", file=sys.stderr)
        return 1
    record_metric(project, "work_done", id=item["id"], lane=item.get("lane"), jira=item.get("jira_key"))
    out(item)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    data = work.load(Project(Path(args.project)))
    items = data["items"] if args.all else [i for i in data["items"] if i.get("status") == "open"]
    out({"active": data.get("active"), "items": items})
    return 0


# ----------------------------------------------------------------------- Jira profile

def parse_types(args: argparse.Namespace) -> list[dict[str, Any]]:
    raw = args.issue_types_json
    if args.issue_types_file:
        raw = Path(args.issue_types_file).read_text(encoding="utf-8")
    types = json.loads(raw or "[]")
    if not isinstance(types, list):
        raise ValueError("issue types must be a JSON list")
    return [t if isinstance(t, dict) else {"name": str(t)} for t in types]


def cmd_profile(args: argparse.Namespace) -> int:
    project = Project(Path(args.project))
    profile = work.load_profile(project)
    action = args.action

    if action == "show":
        out({**profile, "mode": work.profile_mode(project)})
        return 0

    if action == "reset":
        work.profile_path(project).unlink(missing_ok=True)
        print("jira profile removed; the next ticket will rediscover and reconfirm")
        return 0

    if action == "set":
        # Discovery result: replaces the types and re-guesses the mapping, and always drops
        # confirmation -- a changed project shape must be confirmed by the human again.
        types = parse_types(args)
        if not types:
            print("no issue types given", file=sys.stderr)
            return 1
        profile.update({
            "project_key": args.project_key or profile.get("project_key"),
            "issue_types": types,
            "mapping": json.loads(args.mapping_json) if args.mapping_json else work.guess_mapping(types),
            "discovered_at": now_iso(),
            "confirmed": False,
            "auto": False,
        })
        work.save_profile(project, profile)
        out({**profile, "mode": work.profile_mode(project)})
        return 0

    if action == "confirm":
        if not profile.get("issue_types"):
            print("nothing to confirm: run `profile set` after discovering the project's issue types", file=sys.stderr)
            return 1
        if args.project_key:
            profile["project_key"] = args.project_key
        if args.mapping_json:
            profile["mapping"] = {**(profile.get("mapping") or {}), **json.loads(args.mapping_json)}
        if not profile.get("project_key"):
            print("no Jira project key recorded", file=sys.stderr)
            return 1
        if args.default_parent is not None:
            profile["default_parent"] = args.default_parent or None
        profile["confirmed"] = True
        profile["confirmed_at"] = now_iso()
        profile["auto"] = bool(args.auto)
        work.save_profile(project, profile)
        # Make the project's own config agree, so breakdown/ship use the same key.
        config = read_json(project.config_path)
        config["jira_integration"] = True
        config["jira_project_key"] = profile["project_key"]
        if profile["mapping"].get("subtask"):
            config["jira_subtask_type"] = profile["mapping"]["subtask"]
        write_json(project.config_path, config)
        out({**profile, "mode": work.profile_mode(project)})
        return 0

    if action == "auto":
        if not profile.get("confirmed"):
            print("confirm the profile first", file=sys.stderr)
            return 1
        profile["auto"] = args.state == "on"
        work.save_profile(project, profile)
        out({**profile, "mode": work.profile_mode(project)})
        return 0
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func: Any) -> argparse.ArgumentParser:
        p = sub.add_parser(name)
        p.add_argument("--project", default=".")
        p.set_defaults(func=func)
        return p

    p = add("status", cmd_status)
    p.add_argument("--json", action="store_true")
    p = add("classify", cmd_classify)
    p.add_argument("text", nargs="+")

    def sizing(p: argparse.ArgumentParser) -> None:
        p.add_argument("--files", type=int)
        p.add_argument("--tasks", type=int)
        p.add_argument("--deliverables", type=int)
        p.add_argument("--contract", action="store_true", help="changes an API contract or data model")
        p.add_argument("--adr", action="store_true", help="needs a design decision recorded")
        p.add_argument("--signals-json")

    p = add("ticket-plan", cmd_ticket_plan)
    p.add_argument("--lane", required=True, choices=[lane for lane in work.LANES if lane != "question"])
    p.add_argument("--parent")
    sizing(p)

    p = add("open", cmd_open)
    p.add_argument("--title", required=True)
    p.add_argument("--lane", required=True, choices=[lane for lane in work.LANES if lane != "question"])
    p.add_argument("--jira")
    p.add_argument("--jira-type")
    p.add_argument("--parent")
    sizing(p)

    p = add("link", cmd_link)
    p.add_argument("id")
    p.add_argument("key")
    p = add("defer", cmd_defer)
    p.add_argument("id")
    p.add_argument("--reason")
    p = add("switch", cmd_switch)
    p.add_argument("id")
    p = add("done", cmd_done)
    p.add_argument("id", nargs="?")
    p = add("list", cmd_list)
    p.add_argument("--all", action="store_true")

    p = add("profile", cmd_profile)
    p.add_argument("action", choices=["show", "set", "confirm", "auto", "reset"])
    p.add_argument("state", nargs="?", choices=["on", "off"], help="for `auto`")
    p.add_argument("--project-key")
    p.add_argument("--issue-types-json")
    p.add_argument("--issue-types-file")
    p.add_argument("--mapping-json")
    p.add_argument("--default-parent")
    p.add_argument("--auto", action="store_true", help="with `confirm`: create tickets without asking from now on")

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
