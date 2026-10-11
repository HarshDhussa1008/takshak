"""Work items: one ledger entry (and, with Jira on, one ticket) for every change to the codebase.

The pipeline (design -> breakdown -> implement) only covers planned feature work. Real
days are mostly everything else -- a bug report, a "can you also tweak X", a one-line fix --
and those used to bypass the pipeline entirely, so task state drifted and the repo lost its
shape. A work item is the common denominator: whatever the lane, a change is attributed to
an item, and (when Jira is enabled) to a ticket whose type was chosen by the size of the work.

As everywhere in takshak, *code* decides what can be decided deterministically (lane hint,
sizing, mapping a size onto the project's real issue types, whether an edit is covered by a
work item) and the /takshak:work skill does the parts that need judgement or an MCP call
(reading the Jira project, asking the human to confirm, creating the ticket).

State:
    .claude/work_items.json   per developer: {"active", "counter", "items": [...]}  (gitignored)
    .claude/jira_profile.json shared by the team: the confirmed Jira project, its issue types
                              and the size -> type mapping, plus whether auto-mode is on
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from _common import Project, now_iso, read_json, write_json

WORK_NAME = "work_items.json"
PROFILE_NAME = "jira_profile.json"
ITEMS_CAP = 200

LANES = ("question", "trivial", "bug", "change", "feature", "solution")
LEVELS = ("subtask", "task", "story", "epic")  # ascending size; "bug" is a flavour of task


def work_path(project: Project) -> Path:
    return project.state_dir / WORK_NAME


def profile_path(project: Project) -> Path:
    return project.state_dir / PROFILE_NAME


def gate_enabled(project: Project) -> bool:
    return bool(project.config().get("work_item_gate", True))


def jira_on(project: Project) -> bool:
    return bool(project.config().get("jira_integration"))


# --------------------------------------------------------------------------- ledger

def load(project: Project) -> dict[str, Any]:
    data = read_json(work_path(project))
    data.setdefault("counter", 0)
    data.setdefault("active", None)
    data.setdefault("items", [])
    return data


def save(project: Project, data: dict[str, Any]) -> None:
    items = data.get("items") or []
    if len(items) > ITEMS_CAP:  # keep every open item, drop the oldest closed ones
        closed = [i for i in items if i.get("status") == "done"]
        drop = {i["id"] for i in closed[: len(items) - ITEMS_CAP]}
        data["items"] = [i for i in items if i["id"] not in drop]
    write_json(work_path(project), data)


def find(data: dict[str, Any], item_id: str | None) -> dict[str, Any] | None:
    for item in data.get("items") or []:
        if item.get("id") == item_id:
            return item
    return None


def active_item(project: Project) -> dict[str, Any] | None:
    data = load(project)
    item = find(data, data.get("active"))
    return item if item and item.get("status") == "open" else None


def ticket_state(project: Project, item: dict[str, Any]) -> str:
    """'linked' (has a Jira key), 'deferred' (Jira unreachable: ticket to be created later),
    'off' (Jira disabled), or 'missing' (Jira is on and nobody has linked or deferred one)."""
    if not jira_on(project):
        return "off"
    if item.get("jira_key"):
        return "linked"
    if item.get("jira_deferred"):
        return "deferred"
    return "missing"


def open_item(project: Project, title: str, lane: str, *, jira_key: str | None = None,
              jira_type: str | None = None, parent: str | None = None,
              sizing: dict[str, Any] | None = None, make_active: bool = True) -> dict[str, Any]:
    data = load(project)
    data["counter"] = int(data.get("counter") or 0) + 1
    item = {
        "id": f"W-{data['counter']}",
        "title": title.strip()[:200],
        "lane": lane if lane in LANES else "change",
        "status": "open",
        "jira_key": jira_key,
        "jira_type": jira_type,
        "parent_key": parent,
        "sizing": sizing or {},
        "opened_at": now_iso(),
        "closed_at": None,
    }
    data["items"].append(item)
    if make_active:
        data["active"] = item["id"]
    save(project, data)
    return item


def update_item(project: Project, item_id: str, **fields: Any) -> dict[str, Any] | None:
    data = load(project)
    item = find(data, item_id)
    if item is None:
        return None
    item.update(fields)
    save(project, data)
    return item


def switch(project: Project, item_id: str) -> dict[str, Any] | None:
    data = load(project)
    item = find(data, item_id)
    if item is None or item.get("status") != "open":
        return None
    data["active"] = item_id
    save(project, data)
    return item


def close(project: Project, item_id: str | None = None) -> dict[str, Any] | None:
    data = load(project)
    item = find(data, item_id or data.get("active"))
    if item is None:
        return None
    item["status"] = "done"
    item["closed_at"] = now_iso()
    if data.get("active") == item["id"]:
        # fall back to the most recent other open item, if any
        others = [i for i in data["items"] if i.get("status") == "open" and i["id"] != item["id"]]
        data["active"] = others[-1]["id"] if others else None
    save(project, data)
    return item


def plan_covers_edits(project: Project) -> bool:
    """An approved plan with work still open is itself the work item for pipeline implementation.
    Once every task is completed the plan no longer vouches for new edits: a follow-up request
    is a new change and gets its own item."""
    state = read_json(project.task_state)
    tasks = state.get("tasks") or []
    return state.get("approved") is True and any(t.get("status") != "completed" for t in tasks)


# --------------------------------------------------------------------------- lane hint

_QUESTION = re.compile(
    r"^\s*(how|what|why|where|when|which|who|can you explain|could you explain|explain|show me|"
    r"tell me|describe|summari[sz]e|list|is there|are there|does|do you|walk me through)\b", re.I)
_CHANGE_VERB = re.compile(
    r"\b(add|implement|build|create|fix|change|update|modify|refactor|rename|remove|delete|"
    r"replace|migrate|upgrade|bump|tweak|adjust|make it|make the|support|integrate|wire|"
    r"extend|optimi[sz]e|move|extract|handle|enable|disable|patch|rewrite)\b", re.I)
_BUG = re.compile(
    r"\b(bug|broken|crash(es|ed|ing)?|fails?|failing|failure|error|exception|traceback|regression|"
    r"incorrect|wrong|doesn'?t work|not working|isn'?t working|stopped working|500|timeout|"
    r"hang(s|ing)?|leak(s|ing)?|flaky|race condition|fix(es)? (the |a |an )?(bug|issue|crash|error))\b", re.I)
_TRIVIAL = re.compile(
    r"\b(typo|spelling|wording|comment|docstring|log (message|line|level)|rename (a |the )?(var|variable|"
    r"constant)|bump (the )?version|one[- ]liner|small (change|tweak|fix)|minor|cosmetic|format(ting)?|"
    r"whitespace|lint)\b", re.I)
_SOLUTION = re.compile(
    r"\b(platform|end[- ]to[- ]end|from scratch|new service|new system|architecture|re-?architect|"
    r"overhaul|redesign|migration (of|to|from)|multi[- ]?(tenant|region|service)|entire|whole (app|system|service)|"
    r"roadmap|epic|initiative|rollout)\b", re.I)
_FEATURE = re.compile(
    r"\b(feature|endpoint|api|screen|page|workflow|dashboard|integration|capability|support for|"
    r"allow (users?|admins?)|as a user|new (command|flag|option|report|module|screen|page|endpoint))\b", re.I)


def classify(prompt: str) -> dict[str, Any]:
    """Deterministic *hint* about which lane a request belongs to. The skill (and the human)
    have the last word; this only makes sure nobody starts editing without having been asked."""
    text = (prompt or "").strip()
    reasons: list[str] = []
    if not text or text.startswith("/"):
        return {"lane": "question", "reasons": ["empty or slash command"], "confident": True}
    words = len(text.split())
    change = bool(_CHANGE_VERB.search(text))
    if _QUESTION.match(text) and not re.search(r"\b(and|then|please)\b.*\b(fix|change|add|update|implement)\b", text, re.I):
        return {"lane": "question", "reasons": ["asks for information"], "confident": True}
    if not change and not _BUG.search(text):
        return {"lane": "question", "reasons": ["no change requested"], "confident": words < 40}
    if _BUG.search(text):
        reasons.append("describes a defect")
        lane = "bug"
    elif _SOLUTION.search(text) or words > 120:
        reasons.append("reads as a multi-part solution" if _SOLUTION.search(text) else "long, multi-part request")
        lane = "solution"
    elif _TRIVIAL.search(text) and words < 40:
        reasons.append("small cosmetic change")
        lane = "trivial"
    elif _FEATURE.search(text) or words > 45:
        reasons.append("new capability")
        lane = "feature"
    else:
        reasons.append("scoped change")
        lane = "change"
    return {"lane": lane, "reasons": reasons, "confident": lane in ("bug", "trivial")}


# --------------------------------------------------------------------------- sizing

def size_points(signals: dict[str, Any]) -> tuple[int, list[str]]:
    pts, why = 0, []
    files = int(signals.get("files") or 0)
    if files > 15:
        pts += 3
        why.append(f"{files} files")
    elif files >= 7:
        pts += 2
        why.append(f"{files} files")
    elif files >= 3:
        pts += 1
        why.append(f"{files} files")
    if signals.get("contract"):
        pts += 1
        why.append("changes an API contract or data model")
    if signals.get("adr"):
        pts += 1
        why.append("needs a design decision (ADR)")
    tasks = int(signals.get("tasks") or 0)
    if tasks >= 4:
        pts += 1
        why.append(f"{tasks} planned tasks")
    if int(signals.get("deliverables") or 0) >= 2:
        pts += 3
        why.append("multiple independently shippable deliverables")
    return pts, why


_BASE_LEVEL = {"trivial": "subtask", "change": "task", "bug": "task", "feature": "story", "solution": "epic", "question": "subtask"}


def _min_level(points: int) -> str:
    return "epic" if points >= 6 else "story" if points >= 3 else "task" if points >= 1 else "subtask"


def decide(lane: str, signals: dict[str, Any], profile: dict[str, Any] | None,
           parent_key: str | None = None) -> dict[str, Any]:
    """Weigh the work and pick a ticket type: solution -> Epic, feature -> Story, bug/work ->
    Task (or Bug where the project has one), small edit -> Subtask under a parent. Ambiguity
    rounds *up* one step: a mis-sized ticket hides risk, an over-sized one only costs a click."""
    points, why = size_points(signals)
    base = _BASE_LEVEL.get(lane, "task")
    floor = _min_level(points)
    level = max(base, floor, key=LEVELS.index)
    is_bug = lane == "bug" and LEVELS.index(level) <= LEVELS.index("task")
    reasons = [f"lane '{lane}' -> at least {base}"] + ([f"size ({'; '.join(why)}) -> at least {floor}"] if why else [])
    if lane == "bug" and not is_bug:
        reasons.append("defect is large enough to track as more than a bug fix")

    promoted_from = None
    if level == "subtask" and not parent_key:
        promoted_from, level = "subtask", "task"
        reasons.append("no open parent ticket to attach a subtask to -> Task")

    names = resolve_type(level, profile, is_bug=is_bug)
    if names["fell_back_from"]:
        reasons.append(f"project has no '{names['fell_back_from']}' type -> using {names['name']}")
    return {
        "lane": lane, "points": points, "level": level, "is_bug": is_bug,
        "issue_type": names["name"], "promoted_from": promoted_from,
        "needs_parent": level == "subtask", "parent_key": parent_key,
        "reasons": reasons,
    }


_FALLBACK = {
    "epic": ("epic", "story", "task"),
    "story": ("story", "task"),
    "task": ("task", "story"),
    "bug": ("bug", "task"),
    "subtask": ("subtask", "task"),
}


def resolve_type(level: str, profile: dict[str, Any] | None, *, is_bug: bool = False) -> dict[str, Any]:
    """Map an abstract level onto an issue-type name the project really has."""
    key = "bug" if is_bug and level == "task" else level
    mapping = (profile or {}).get("mapping") or {}
    existing = {t.get("name") for t in (profile or {}).get("issue_types") or []}
    chain = _FALLBACK.get(key, ("task",))
    for i, step in enumerate(chain):
        name = mapping.get(step)
        if name and (not existing or name in existing):
            return {"name": name, "fell_back_from": chain[0] if i else None}
    default = {"epic": "Epic", "story": "Story", "task": "Task", "bug": "Bug", "subtask": "Subtask"}[key if key in _FALLBACK else "task"]
    return {"name": default, "fell_back_from": None}


_SYNONYMS = {
    "epic": ("epic", "initiative", "theme", "program", "programme"),
    "story": ("story", "user story", "feature", "enhancement", "improvement", "new feature"),
    "task": ("task", "work item", "work"),
    "bug": ("bug", "defect", "incident"),
    "subtask": ("sub-task", "subtask", "sub task", "sub-work item"),
}


def guess_mapping(issue_types: list[dict[str, Any]]) -> dict[str, str | None]:
    """First guess at level -> project issue type, from names and Jira's hierarchy metadata.
    It is only ever a *proposal*: the skill makes the human confirm it once per project."""
    mapping: dict[str, str | None] = {k: None for k in _SYNONYMS}
    for level, names in _SYNONYMS.items():
        for t in issue_types:
            name = str(t.get("name") or "")
            if name.lower() in names:
                mapping[level] = name
                break
    for t in issue_types:  # hierarchy metadata fills the gaps (renamed types)
        name, hier, is_sub = str(t.get("name") or ""), t.get("hierarchyLevel"), bool(t.get("subtask"))
        if is_sub and not mapping["subtask"]:
            mapping["subtask"] = name
        elif hier == 1 and not mapping["epic"]:
            mapping["epic"] = name
    return mapping


def load_profile(project: Project) -> dict[str, Any]:
    return read_json(profile_path(project))


def save_profile(project: Project, profile: dict[str, Any]) -> None:
    write_json(profile_path(project), profile)


def profile_mode(project: Project) -> str:
    """'none' (never discovered), 'unconfirmed' (discovered, human has not confirmed),
    'confirm' (confirmed; ask before each ticket), or 'auto' (confirmed; create without asking)."""
    p = load_profile(project)
    if not p.get("issue_types"):
        return "none"
    if not p.get("confirmed"):
        return "unconfirmed"
    return "auto" if p.get("auto") else "confirm"


# --------------------------------------------------------------------------- Bash writes

_REDIRECT = re.compile(r"(?<![0-9&<>])(?:\d?>>?|&>>?)\s*(?!&)([^\s;|&<>()]+)")
_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
_PY_OPEN_W = re.compile(r"""open\(\s*['"]([^'"]+)['"]\s*,\s*['"][wa]""")
_PY_PATH_W = re.compile(r"""Path\(\s*['"]([^'"]+)['"]\s*\)\.write_(?:text|bytes)""")


def bash_write_targets(command: str) -> list[str]:
    """Best-effort list of file paths a shell command writes. Deliberately conservative -- it
    only recognises clear patterns (redirects, tee, sed -i, cp/mv/install destinations,
    git apply/checkout --, patch), because a gate that cries wolf gets switched off."""
    targets: list[str] = []
    command = command or ""
    if "<<" in command:  # heredoc: the body is data, not commands
        lines = command.split("\n")
        for i, ln in enumerate(lines):
            if "<<" in ln:
                command = "\n".join(lines[: i + 1])
                break
    for segment in re.split(r"&&|\|\||;|\n|\|", command):
        segment = segment.strip()
        if not segment:
            continue
        masked = _QUOTED.sub('""', segment)  # a ">" inside quotes is text, not a redirect
        for m in _REDIRECT.finditer(masked):
            targets.append(m.group(1).strip("'\""))
        try:
            parts = shlex.split(segment, posix=True)
        except ValueError:
            parts = segment.split()
        if not parts:
            continue
        while parts and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", parts[0]):  # leading VAR=value
            parts = parts[1:]
        if not parts:
            continue
        cmd = Path(parts[0]).name
        args = [a for a in parts[1:] if not a.startswith("-") or a == "-"]
        flags = [a for a in parts[1:] if a.startswith("-")]
        if cmd == "tee":
            targets += args
        elif cmd in ("sed", "perl") and any(f.startswith("-i") or f.startswith("-pi") or "-i" in f for f in flags):
            targets += args[1:] if cmd == "sed" and len(args) > 1 else args[-1:]
        elif cmd in ("cp", "mv", "install", "ln") and len(args) >= 2:
            targets.append(args[-1])
        elif cmd in ("touch", "truncate"):
            targets += args
        elif cmd == "git" and len(parts) > 1 and parts[1] in ("apply", "am"):
            targets.append("<patch>")
        elif cmd == "patch":
            targets.append("<patch>")
        elif cmd == "dd":
            targets += [a[3:] for a in parts[1:] if a.startswith("of=")]
        elif cmd in ("python", "python3"):
            targets += _PY_OPEN_W.findall(segment) + _PY_PATH_W.findall(segment)
    out = []
    for t in targets:
        t = t.strip("'\"")
        if t and not t.startswith("/dev/") and t not in ("-", "/dev/null"):
            out.append(t)
    return out
