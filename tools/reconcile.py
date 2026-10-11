"""Takshak reconcile -- the deterministic half of /takshak:reconcile.

    run.sh tools/reconcile.py status --project DIR [--json]
    run.sh tools/reconcile.py check  --project DIR          # exit 1 when docs are behind the code
    run.sh tools/reconcile.py mark   --project DIR [--final] [--doc PATH]  # docs now match the code
    run.sh tools/reconcile.py inventory --project DIR [--path SUBDIR] [--json]  # map an existing codebase

`status` answers "what changed since the docs were last reconciled, and which sections of the
Implementation Guide does that touch?" from git plus the edit tracker -- no LLM, so it is
safe to call from a hook, the ship gate and the dashboard. `mark` is what the skill runs as
its last step, once the guide has actually been brought up to date. The skill itself does the
part that needs judgement: reading the code and rewriting the as-built docs.

`inventory` is the first step of adopting an *existing* codebase (reverse engineering): a
deterministic map of what is there -- manifests, modules, entry points, routes, tests, existing
docs -- split into batches small enough to read one at a time (or hand to parallel subagents),
so the skill does not have to discover the codebase by wandering through it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

from _common import Project, git, record_metric  # noqa: E402
from _reconcile import (  # noqa: E402
    DOC_SUFFIXES,
    NON_SOURCE_SUFFIXES,
    SKIP_DIRS,
    TEST_DIRS,
    TEST_NAME,
    impl_dir,
    is_source_file,
    mark,
    sdd_dir,
    status,
)

CONTAINER_DIRS = {"src", "lib", "app", "apps", "pkg", "packages", "internal", "cmd", "services", "modules", "libs"}
MANIFESTS = {
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "package.json", "tsconfig.json", "go.mod",
    "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts", "Gemfile", "composer.json", "Dockerfile",
    "docker-compose.yml", "docker-compose.yaml", "Makefile", "openapi.yaml", "openapi.yml", "openapi.json",
    "swagger.yaml", "swagger.json", "schema.graphql",
}
ENTRY_PATTERNS: dict[str, re.Pattern[str]] = {
    "main": re.compile(r"__name__\s*==\s*['\"]__main__['\"]|^func main\(|^fn main\(|static void main\(|^if \[\[.*\$0", re.M),
    "http_route": re.compile(
        r"@\w+\.(?:route|get|post|put|delete|patch)\(|\b(?:app|router|server)\.(?:get|post|put|delete|patch|use)\(|"
        r"http\.HandleFunc|\.Handle\(|@(?:Get|Post|Put|Delete|Patch|Request)Mapping|\bre_path\(|\bpath\(['\"]|"
        r"BaseHTTPRequestHandler|FastAPI\(|Flask\(|express\(\)"),
    "cli": re.compile(r"argparse\.ArgumentParser|@click\.(?:command|group)|typer\.|cobra\.Command|clap::|commander"),
    "background_task": re.compile(r"@\w*\.task\b|@shared_task|cron|schedule\.every|@Scheduled|setInterval\("),
    "event_consumer": re.compile(r"\.subscribe\(|@KafkaListener|@SqsListener|consume\(|on_message|\.on\(['\"]"),
}
MAX_BATCH_FILES = 25
MAX_BATCH_LOC = 4000
MAX_READ = 400_000


def cmd_status(args: argparse.Namespace) -> int:
    result = status(Project(Path(args.project).expanduser().resolve()))
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    if not result["enabled"]:
        print("reconcile: disabled (set \"reconcile\": true in .claude/framework.json)")
        return 0
    guide = result["impl_doc"] + ("" if result["impl_doc_exists"] else "  (not created yet)")
    print(f"Implementation Guide: {guide}")
    print(f"Needs: {result['needs']}  |  changed source files: {len(result['changed'])}  |  due: {result['due']}")
    if result["stale_sections"]:
        print("Stale sections: " + "; ".join(result["stale_sections"]))
    if result["undocumented"]:
        print("Not covered by any section: " + ", ".join(result["undocumented"][:15]))
    if result["completed_unreconciled"]:
        print("Tasks completed since last reconcile: " + ", ".join(result["completed_unreconciled"]))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    result = status(Project(Path(args.project).expanduser().resolve()))
    if not result["enabled"] or result["needs"] == "none":
        print("[reconcile] docs are in sync with the code")
        return 0
    why = "no Implementation Guide yet" if result["needs"] == "init" else f"{len(result['changed'])} source file(s) changed since the last reconcile"
    print(f"[reconcile] docs are behind the code: {why}. Run /takshak:reconcile.")
    return 1


def _is_test_file(rel: str) -> bool:
    posix = PurePosixPath(rel)
    return bool((set(posix.parts) & TEST_DIRS) or TEST_NAME.search(posix.name))


def _list_files(project: Project, sub: str) -> list[str]:
    out = git(project.root, "ls-files", "-co", "--exclude-standard", "--", sub or ".", timeout=30)
    if out:
        return sorted(set(out.splitlines()))
    base = project.root / sub if sub else project.root  # not a git repo: walk it
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        found.extend((Path(dirpath) / f).relative_to(project.root).as_posix() for f in filenames)
    return sorted(found)


def _module_key(rel: str) -> str:
    parts = PurePosixPath(rel).parts
    if len(parts) == 1:
        return "(root)"
    if parts[0] in CONTAINER_DIRS and len(parts) > 2:
        return f"{parts[0]}/{parts[1]}/"
    return f"{parts[0]}/"


def inventory(project: Project, sub: str = "") -> dict[str, Any]:
    """Deterministic map of an existing codebase: no judgement, just what is on disk."""
    sub = sub.strip("/")
    files = _list_files(project, sub)
    sources = [f for f in files if is_source_file(project, f)]
    tests = [f for f in files if _is_test_file(f) and PurePosixPath(f).suffix.lower() not in DOC_SUFFIXES | NON_SOURCE_SUFFIXES]
    manifests = [f for f in files if PurePosixPath(f).name in MANIFESTS or PurePosixPath(f).suffix in {".proto", ".graphql"}
                 or f.startswith(".github/workflows/")]
    docs = [f for f in files if PurePosixPath(f).suffix.lower() in DOC_SUFFIXES
            and not f.startswith((impl_dir(project) + "/", sdd_dir(project) + "/"))][:60]

    modules: dict[str, dict[str, Any]] = {}
    entry_points: list[dict[str, object]] = []
    for rel in sources:
        path = project.root / rel
        try:
            text = path.read_bytes()[:MAX_READ].decode("utf-8", errors="replace")
        except OSError:
            continue
        if "\x00" in text[:2000]:
            continue  # binary
        loc = text.count("\n") + 1
        kinds = [k for k, rx in ENTRY_PATTERNS.items() if rx.search(text)]
        mod = modules.setdefault(_module_key(rel), {"path": _module_key(rel), "files": [], "loc": 0, "langs": {}, "entry_kinds": set()})
        mod["files"].append(rel)
        mod["loc"] = int(mod["loc"]) + loc
        suffix = PurePosixPath(rel).suffix or "(none)"
        mod["langs"][suffix] = mod["langs"].get(suffix, 0) + 1
        mod["entry_kinds"].update(kinds)
        if kinds:
            entry_points.append({"file": rel, "kinds": kinds})

    stems = {PurePosixPath(f).stem: key for key, m in modules.items() for f in m["files"]}
    unmatched = 0
    for t in tests:
        stem = re.sub(r"^test_|_test$|\.test$|\.spec$|^test", "", PurePosixPath(t).stem)
        owner = stems.get(stem)
        if owner:
            modules[owner].setdefault("tests", []).append(t)
        else:
            unmatched += 1

    mod_list = sorted(modules.values(), key=lambda m: -int(m["loc"]))
    for m in mod_list:
        m["entry_kinds"] = sorted(m["entry_kinds"])
        m["file_count"] = len(m["files"])
        m.setdefault("tests", [])

    batches: list[list[str]] = []
    current: list[str] = []
    cur_files = cur_loc = 0
    for m in mod_list:
        n, loc = int(m["file_count"]), int(m["loc"])
        oversized = n > MAX_BATCH_FILES or loc > MAX_BATCH_LOC
        if oversized:
            m["split"] = True  # too big for one reader: the skill should split by sub-package
        if current and (oversized or cur_files + n > MAX_BATCH_FILES or cur_loc + loc > MAX_BATCH_LOC):
            batches.append(current)
            current, cur_files, cur_loc = [], 0, 0
        current.append(str(m["path"]))
        cur_files, cur_loc = cur_files + n, cur_loc + loc
        if oversized:
            batches.append(current)
            current, cur_files, cur_loc = [], 0, 0
    if current:
        batches.append(current)

    return {
        "project": project.config().get("project_name"),
        "scope": sub or ".",
        "totals": {"source_files": len(sources), "loc": sum(int(m["loc"]) for m in mod_list), "test_files": len(tests),
                   "unmatched_tests": unmatched, "modules": len(mod_list)},
        "manifests": manifests,
        "existing_docs": docs,
        "entry_points": entry_points,
        "modules": mod_list,
        "batches": batches,
        "parallel_recommended": len(batches) > 2,
    }


def cmd_inventory(args: argparse.Namespace) -> int:
    project = Project(Path(args.project).expanduser().resolve())
    result = inventory(project, args.path or "")
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    t = result["totals"]
    print(f"Scope {result['scope']}: {t['source_files']} source files, {t['loc']} lines, {t['test_files']} test files, {t['modules']} modules")  # type: ignore[index]
    print("Manifests: " + (", ".join(result["manifests"][:12]) or "none found"))  # type: ignore[index]
    for m in result["modules"]:  # type: ignore[attr-defined]
        kinds = f"  [{', '.join(m['entry_kinds'])}]" if m["entry_kinds"] else ""
        print(f"  {m['path']:<28} {m['file_count']:>4} files {m['loc']:>7} loc{kinds}{'  (split)' if m.get('split') else ''}")
    print(f"Batches: {len(result['batches'])}" + ("  (use parallel subagents)" if result["parallel_recommended"] else ""))
    return 0


def cmd_mark(args: argparse.Namespace) -> int:
    project = Project(Path(args.project).expanduser().resolve())
    before = status(project)
    mark(project, "final" if args.final else "update", args.doc)
    record_metric(
        project, "reconcile",
        mode="final" if args.final else before["needs"],
        files=len(before["changed"]), stale_sections=len(before["stale_sections"]),
        undocumented=len(before["undocumented"]),
    )
    print(f"[reconcile] baseline recorded; {len(before['changed'])} changed file(s) now reflected in {status(project)['impl_doc']}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("status", cmd_status), ("check", cmd_check), ("mark", cmd_mark), ("inventory", cmd_inventory)):
        p = sub.add_parser(name)
        p.add_argument("--project", required=True)
        p.set_defaults(func=func)
        if name == "status":
            p.add_argument("--json", action="store_true")
        if name == "mark":
            p.add_argument("--final", action="store_true", help="the whole feature is implemented; this is the closing pass")
            p.add_argument("--doc", help="record where this project's Implementation Guide lives (adopting a legacy codebase)")
        if name == "inventory":
            p.add_argument("--path", help="limit the map to one subdirectory (a service in a monorepo)")
            p.add_argument("--json", action="store_true")
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
