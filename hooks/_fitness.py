"""Architecture fitness functions: the shape of the code, enforced as tests.

A repository loses its shape one convenient import at a time: the HTTP layer reaches into the
database, a "core" package starts importing a web framework, two modules grow a cycle, a file
becomes a thousand-line junk drawer. Reviews catch some of it, once. A fitness rule catches all
of it, every time, at the moment of the edit.

Rules live in `.claude/fitness.json` (commit it -- it is the team's architecture, written down):

    {"rules": [
      {"id": "api-no-db",  "description": "HTTP layer must not import storage", "from": "app/api/", "forbid": ["app/db/", "sqlalchemy"]},
      {"id": "layers",     "type": "layers", "order": ["app/api/", "app/services/", "app/db/"]},
      {"id": "no-cycles",  "type": "no-cycles", "scope": "app/"},
      {"id": "file-size",  "type": "max-lines", "scope": "app/", "max": 600}
    ],
     "baseline": ["api-no-db|app/api/legacy.py|imports app/db/session"]}

`forbid` entries ending in `/` are repo paths; anything else is an external package name.
`layers` lists layers top to bottom: a layer may import itself and layers *below* it, never above.
`baseline` is a ratchet: violations that already existed when the rules were adopted are tolerated,
every new one fails. Imports are read with `ast` for Python and by pattern for JS/TS; other
languages are covered by `forbid` on raw import text and by `max-lines`.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from _common import Project, read_json
from _reconcile import SKIP_DIRS

RULES_NAME = "fitness.json"
CODE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".java", ".kt", ".rb", ".rs", ".cs"}
JS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
JS_IMPORT = re.compile(r"""(?:import\s+(?:[^'"]*?\s+from\s+)?|export\s+[^'"]*?\s+from\s+|require\(\s*|import\(\s*)['"]([^'"]+)['"]""")
GENERIC_IMPORT = re.compile(r"""^\s*(?:import|using|use|from)\s+["']?([A-Za-z_][\w./:\-@]*)""", re.MULTILINE)
MAX_FILES = 4000
MAX_BYTES = 400_000


def rules_path(project: Project) -> Path:
    return project.state_dir / RULES_NAME


def load_rules(project: Project) -> dict[str, Any]:
    data = read_json(rules_path(project))
    data.setdefault("rules", [])
    data.setdefault("baseline", [])
    return data


def source_files(project: Project, scope: str = "") -> list[str]:
    root = project.root
    out: list[str] = []
    for path in root.rglob("*"):
        if len(out) >= MAX_FILES:
            break
        if path.suffix.lower() not in CODE_SUFFIXES or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if set(PurePosixPath(rel).parts) & SKIP_DIRS:
            continue
        if scope and not rel.startswith(scope):
            continue
        out.append(rel)
    return sorted(out)


def read_text(project: Project, rel: str) -> str:
    path = project.root / rel
    try:
        if path.stat().st_size > MAX_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def python_imports(rel: str, text: str) -> list[str]:
    """Imported modules as repo-style paths ('app/db/session') or bare package names."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    here = PurePosixPath(rel).parent
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = here
                for _ in range(node.level - 1):
                    base = base.parent
                mod = (base / node.module.replace(".", "/")).as_posix() if node.module else base.as_posix()
                out.append(mod)
                out += [f"{mod}/{a.name}" for a in node.names if a.name != "*"]
            elif node.module:
                out.append(node.module)
                out += [f"{node.module}.{a.name}" for a in node.names if a.name != "*"]
    return out


def js_imports(rel: str, text: str) -> list[str]:
    here = PurePosixPath(rel).parent
    out = []
    for spec in JS_IMPORT.findall(text):
        if spec.startswith("."):
            parts: list[str] = []
            for p in (here / spec).parts:
                if p == "..":
                    if parts:
                        parts.pop()
                elif p != ".":
                    parts.append(p)
            out.append("/".join(parts))
        else:
            out.append(spec)
    return out


def imports_of(rel: str, text: str) -> list[str]:
    suffix = PurePosixPath(rel).suffix.lower()
    if suffix == ".py":
        return python_imports(rel, text)
    if suffix in JS_SUFFIXES:
        return js_imports(rel, text)
    return GENERIC_IMPORT.findall(text)


def as_path(imp: str) -> str:
    """Normalise an import to a slash path so dotted Python modules compare with repo prefixes."""
    return imp.replace(".", "/") if re.fullmatch(r"[A-Za-z_][\w.]*", imp) else imp


def matches(imp: str, target: str) -> bool:
    if target.endswith("/"):
        p = as_path(imp)
        return p.startswith(target) or (p + "/").startswith(target)
    root = re.split(r"[./]", imp.lstrip("@"), maxsplit=1)[0] if not imp.startswith("@") else "/".join(imp.split("/")[:2])
    return imp == target or root == target or imp.startswith(target + "/") or imp.startswith(target + ".")


def key(rule: str, rel: str, detail: str) -> str:
    return f"{rule}|{rel}|{detail}"


def import_graph(project: Project, scope: str) -> dict[str, set[str]]:
    files = source_files(project, scope)
    known = set(files)
    mods: dict[str, str] = {}
    for f in files:
        stem = PurePosixPath(f).with_suffix("").as_posix()
        mods[stem] = f
        if stem.endswith("/__init__"):
            mods[stem[: -len("/__init__")]] = f
        if PurePosixPath(f).stem == "index":
            mods[str(PurePosixPath(f).parent)] = f
    graph: dict[str, set[str]] = {f: set() for f in files}
    for f in files:
        for imp in imports_of(f, read_text(project, f)):
            target = mods.get(as_path(imp))
            if target and target != f and target in known:
                # a package's __init__ is imported by its own members as a matter of course
                if PurePosixPath(target).name == "__init__.py" and f.startswith(str(PurePosixPath(target).parent) + "/"):
                    continue
                graph[f].add(target)
    return graph


def cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Strongly connected components of size > 1 (Tarjan, iterative)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    out: list[list[str]] = []
    counter = 0
    for start in graph:
        if start in index:
            continue
        work = [(start, iter(sorted(graph[start])))]
        index[start] = low[start] = counter
        counter += 1
        stack.append(start)
        on.add(start)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on.add(nxt)
                    work.append((nxt, iter(sorted(graph[nxt]))))
                    advanced = True
                    break
                if nxt in on:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                if len(comp) > 1:
                    out.append(sorted(comp))
    return sorted(out)


def check(project: Project, only: set[str] | None = None, include_baseline: bool = False) -> list[dict[str, str]]:
    """Violations (not in the baseline unless asked). `only` limits file-level rules to those
    files -- the cheap path used right after an edit; cycle detection needs the whole graph and
    is skipped then."""
    cfg = load_rules(project)
    baseline = set(cfg["baseline"])
    found: list[dict[str, str]] = []

    def add(rule: dict[str, Any], rel: str, detail: str) -> None:
        k = key(rule["id"], rel, detail)
        if include_baseline or k not in baseline:
            found.append({"rule": rule["id"], "file": rel, "detail": detail, "key": k,
                          "description": str(rule.get("description") or "")})

    for rule in cfg["rules"]:
        rtype = rule.get("type") or ("import" if rule.get("forbid") else "")
        if rtype not in {"import", "layers", "max-lines", "no-cycles"} or (rtype == "import" and not rule.get("forbid")):
            # a rule that silently checks nothing is worse than no rule
            found.append({"rule": str(rule.get("id") or "?"), "file": ".claude/fitness.json",
                          "detail": "invalid rule (needs `from`+`forbid`, or type layers / no-cycles / max-lines); it is not being enforced",
                          "key": key(str(rule.get("id") or "?"), ".claude/fitness.json", "invalid"), "description": ""})
            continue
        scope = str(rule.get("from") or rule.get("scope") or "")
        files = [f for f in source_files(project, scope) if only is None or f in only]
        if rtype == "import":
            for f in files:
                seen: set[str] = set()  # `from a.b import c` yields a.b and a.b.c: report the statement once
                for imp in imports_of(f, read_text(project, f)):
                    for target in rule.get("forbid") or []:
                        if str(target) not in seen and matches(imp, str(target)):
                            seen.add(str(target))
                            add(rule, f, f"imports {imp}")
        elif rtype == "layers":
            order = [str(x) for x in rule.get("order") or []]
            for f in [f for f in source_files(project) if only is None or f in only]:
                mine = next((i for i, layer in enumerate(order) if f.startswith(layer)), None)
                if mine is None:
                    continue
                seen_layers: set[str] = set()
                for imp in imports_of(f, read_text(project, f)):
                    for i, layer in enumerate(order):
                        if i < mine and layer not in seen_layers and matches(imp, layer):
                            seen_layers.add(layer)
                            add(rule, f, f"{order[mine]} imports upward into {layer} ({imp})")
        elif rtype == "max-lines":
            limit = int(rule.get("max") or 600)
            for f in files:
                n = read_text(project, f).count("\n") + 1
                if n > limit:
                    add(rule, f, f"{n} lines (max {limit})")
        elif rtype == "no-cycles" and only is None:
            for comp in cycles(import_graph(project, scope)):
                add(rule, comp[0], "cycle: " + " <-> ".join(comp[:5]) + (" ..." if len(comp) > 5 else ""))
    return found


def baseline_now(project: Project) -> int:
    """Adopt the rules on an existing codebase: tolerate what is there, fail anything new."""
    cfg = load_rules(project)
    cfg["baseline"] = sorted({v["key"] for v in check(project, include_baseline=True)})
    path = rules_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return len(cfg["baseline"])
