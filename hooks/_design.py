"""Staged design: freeze, LLD derivation and the design-readiness check.

A first-draft SDD is not a finished design. The honest pipeline is:

    discovery notes -> requirements -> options as ADRs -> HLD (the SDD) -> adversarial hardening
        -> FREEZE -> LLD derived from the frozen design -> readiness check -> reviewer pack -> approval

Everything here is deterministic. The skills do the thinking (interview, options, writing); this
module answers the questions that must not depend on a model's mood:

* *Freeze*: a hash of the design-bearing SDD sections plus the accepted ADR decisions. If either
  changes after the freeze, the LLD that was derived from it is no longer trustworthy, and the
  readiness check says so.
* *LLD stamp*: the LLD carries `<!-- frozen-from: HASH -->`; a stale or missing stamp fails.
* *Readiness*: the checklist a staff reviewer would apply before letting implementation start --
  testable requirements, real alternatives behind each decision, a threat model, a rollback plan,
  no unresolved questions, no template placeholders -- run as code.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from typing import Any

import _adr as adr
import _trace as trace
from _common import Project, now_iso, read_json, write_json

FROZEN_SECTIONS = (
    "Overview", "Goals", "Non-Goals", "Requirements", "Architecture", "Module Map", "Key Flows",
    "Data Models", "API Contracts", "Detailed Design", "Error Handling", "Observability", "Security",
    "Rollout and Rollback",
)
FAILURE_CLASSES = (
    ("Concurrency / races", "concurrency"), ("Partial failure / retries", "partial failure"),
    ("Rollback / migration", "rollback"), ("Idempotency", "idempotency"), ("Auth / authz", "auth"),
    ("Quota / limits / cost", "quota"), ("Clock / ordering", "clock"), ("Data integrity", "data integrity"),
    ("Observability gaps", "observability"), ("Backward compatibility", "backward"),
)
PLACEHOLDER_RE = re.compile(r"\{\{[^}\n]+\}\}|\bTBD\b|\bTODO\b|\?\?\?")
FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
STAMP_RE = re.compile(r"<!--\s*frozen-from:\s*([0-9a-f]{6,64})\s*-->")
HTTP_ENTRY_RE = re.compile(r"^\s*(?:[-*#]+\s*)?`?\b(GET|POST|PUT|PATCH|DELETE)\b\s+(/\S*)", re.MULTILINE)


def sections(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in re.finditer(r"^##\s+(.+?)\s*$(.*?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL):
        out[m.group(1).strip()] = m.group(2).strip()
    return out


def prose(text: str) -> str:
    return FENCE_RE.sub("", text)


def slug_of(sdd_rel: str) -> str:
    return PurePosixPath(sdd_rel).stem


def lld_rel(project: Project, sdd_rel: str) -> str:
    return f"{str(project.config().get('lld_path') or 'docs/lld').strip('/')}/{slug_of(sdd_rel)}.md"


def notes_rel(project: Project, sdd_rel: str) -> str:
    return f"{str(project.config().get('notes_path') or 'docs/design-notes').strip('/')}/{slug_of(sdd_rel)}.md"


def freeze_rel(sdd_rel: str) -> str:
    return str(PurePosixPath(sdd_rel).with_suffix(".freeze.json"))


def read(project: Project, rel: str) -> str:
    try:
        return (project.root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def design_hash(project: Project, sdd_rel: str) -> str:
    secs = sections(read(project, sdd_rel))
    parts = [f"## {name}\n" + re.sub(r"\s+", " ", secs.get(name, "")) for name in FROZEN_SECTIONS]
    for a in adr.load_all(project):
        if adr.is_live(a):
            decision = re.sub(r"\s+", " ", adr.section(a["text"], "Decision"))
            parts.append(f"ADR-{a['number']:04d} accepted: {decision}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def freeze(project: Project, sdd_rel: str) -> dict[str, Any]:
    record = {"sdd": sdd_rel, "hash": design_hash(project, sdd_rel), "frozen_at": now_iso(),
              "adrs": [a["number"] for a in adr.load_all(project) if adr.is_live(a)]}
    write_json(project.root / freeze_rel(sdd_rel), record)
    return record


def freeze_state(project: Project, sdd_rel: str) -> dict[str, Any]:
    rec = read_json(project.root / freeze_rel(sdd_rel))
    if not rec.get("hash"):
        return {"frozen": False, "intact": False, "hash": None}
    now = design_hash(project, sdd_rel)
    return {"frozen": True, "intact": now == rec["hash"], "hash": rec["hash"], "current": now, "frozen_at": rec.get("frozen_at")}


def lld_state(project: Project, sdd_rel: str) -> dict[str, Any]:
    rel = lld_rel(project, sdd_rel)
    text = read(project, rel)
    fs = freeze_state(project, sdd_rel)
    stamp = STAMP_RE.search(text)
    return {
        "path": rel, "exists": bool(text), "stamp": stamp.group(1) if stamp else None,
        "current": bool(stamp and fs["frozen"] and stamp.group(1) == fs["hash"]),
    }


def stamp_lld(project: Project, sdd_rel: str) -> str | None:
    fs = freeze_state(project, sdd_rel)
    path = project.root / lld_rel(project, sdd_rel)
    if not fs["frozen"] or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    tag = f"<!-- frozen-from: {fs['hash']} -->"
    text = STAMP_RE.sub(tag, text, count=1) if STAMP_RE.search(text) else re.sub(r"(^# .+$)", rf"\1\n\n{tag}", text, count=1, flags=re.MULTILINE)
    path.write_text(text, encoding="utf-8")
    return fs["hash"]


# --------------------------------------------------------------------------- hardening

def risk_rows(text: str) -> list[list[str]]:
    body = sections(text).get("Risk Register", "")
    rows = [trace.split_row(ln) for ln in body.splitlines() if ln.strip().startswith("|")]
    return [r for r in rows[2:] if len(r) >= 4 and not set("".join(r)) <= set("-: ")]


def hardening_problems(text: str) -> list[str]:
    """The breakdown gate, as code: Risk Register present, all 10 classes have a verdict,
    nothing Critical left unmitigated."""
    rows = risk_rows(text)
    if not rows:
        return ["Risk Register is missing or empty -- the design has not been through adversarial hardening"]
    blob = " ".join(" ".join(r) for r in rows).lower()
    out = [f"failure class '{name}' has no verdict in the Risk Register" for name, key in FAILURE_CLASSES if key not in blob]
    for r in rows:
        severity, status = (r[2] if len(r) > 2 else "").lower(), (r[4] if len(r) > 4 else "").lower()
        if "critical" in severity and not (status.startswith(("mitigated", "ok", "accepted"))):
            out.append(f"Critical risk '{r[0][:50]}' is not mitigated")
        if not (r[3] if len(r) > 3 else "").strip():
            out.append(f"risk '{r[0][:50]}' has no mitigation")
    return out


# --------------------------------------------------------------------------- readiness

def check(name: str, ok: bool, detail: str = "") -> dict[str, Any]:
    return {"check": name, "ok": ok, "detail": "" if ok else detail}


def open_questions(body: str) -> list[str]:
    out = []
    for ln in body.splitlines():
        s = ln.strip()
        if not s or s.lower().startswith(("(", "_")):
            continue
        if re.match(r"^[-*]\s*\[[xX]\]", s) or re.search(r"\bresolved\b|\banswered\b|→\s*decided", s, re.IGNORECASE):
            continue
        if re.match(r"^[-*]\s", s) or re.match(r"^\d+\.\s", s):
            out.append(s)
    return out


def api_entries_without_errors(body: str) -> list[str]:
    ms = list(HTTP_ENTRY_RE.finditer(body))
    bad = []
    for i, m in enumerate(ms):
        block = body[m.start(): ms[i + 1].start() if i + 1 < len(ms) else len(body)]
        if not re.search(r"\b(error|[45]\d\d)\b", block, re.IGNORECASE):
            bad.append(f"{m.group(1)} {m.group(2)}")
    return bad


def readiness(project: Project, sdd_rel: str) -> list[dict[str, Any]]:
    text = read(project, sdd_rel)
    secs = sections(text)
    checks: list[dict[str, Any]] = []
    if not text:
        return [check("SDD exists", False, f"{sdd_rel} not found")]

    def body(name: str) -> str:
        return prose(secs.get(name, "")).strip()

    def filled(raw: str, minimum: int) -> bool:
        """Real content: long enough, and not a template placeholder (fences included)."""
        return len(prose(raw).strip()) > minimum and not PLACEHOLDER_RE.search(raw)

    # requirements ---------------------------------------------------------------
    reqs = trace.parse_requirements(text)
    checks.append(check("Requirements have IDs", bool(reqs), "no `## Requirements` table with IDs (R1, NFR1, ...)"))
    weak = [r["id"] for r in reqs if not trace.DEFERRED_RE.search(r["priority"]) and (len(r["acceptance"].strip()) < 8 or PLACEHOLDER_RE.search(r["acceptance"] + r["text"]))]
    checks.append(check("Every requirement is testable", not weak, f"acceptance criterion missing or placeholder: {', '.join(weak)}"))
    nfr = [r for r in reqs if r["id"].startswith("NFR")]
    checks.append(check("Non-functional requirements are numeric", bool(nfr) and all(re.search(r"\d", r["acceptance"] + r["text"]) for r in nfr),
                        "add at least one NFR (latency, availability, scale or cost) and give each a number"))
    checks.append(check("Non-goals stated", filled(secs.get("Non-Goals", ""), 10), "`## Non-Goals` is empty"))

    # structure --------------------------------------------------------------------
    arch = secs.get("Architecture", "")
    arrows = len(re.findall(r"-->|---|==>|-\.->", arch))
    checks.append(check("Architecture diagram", "```mermaid" in arch and arrows >= 1 and not PLACEHOLDER_RE.search(arch), "`## Architecture` needs a mermaid diagram with at least two connected components"))
    flows = secs.get("Key Flows", "")
    checks.append(check("Key flows incl. a failure path", "sequenceDiagram" in flows and not PLACEHOLDER_RE.search(flows) and re.search(r"fail|error|timeout|retry", flows, re.IGNORECASE) is not None,
                        "`## Key Flows` needs a sequenceDiagram and a failure path"))
    mm = [ln for ln in secs.get("Module Map", "").splitlines() if ln.strip().startswith("|")][2:]
    checks.append(check("Module map", bool(mm) and not PLACEHOLDER_RE.search("\n".join(mm)), "`## Module Map` has no filled rows"))
    api_body = secs.get("API Contracts", "")
    checks.append(check("API contracts present", filled(api_body, 20),
                        "`## API Contracts` is empty (write `No external interfaces: <why>` if true)"))
    bad_api = api_entries_without_errors(api_body)
    checks.append(check("Every endpoint specifies its errors", not bad_api, f"no error responses for: {', '.join(bad_api)}"))
    checks.append(check("Data models present", filled(secs.get("Data Models", ""), 15), "`## Data Models` is empty (write `No persistent data: <why>` if true)"))

    # decisions --------------------------------------------------------------------
    adrs = adr.load_all(project)
    known = {r["id"] for r in reqs}
    problems = adr.check(project, known or None)
    checks.append(check("Decisions recorded as ADRs", any(adr.is_live(a) for a in adrs), "no accepted ADR: record the significant decisions with `tools/adr.py new`"))
    checks.append(check("ADRs are sound (alternatives, no placeholders)", not problems, "; ".join(problems[:3])))
    undecided = [f"ADR-{a['number']:04d}" for a in adrs if a["status"].lower() == "proposed"]
    checks.append(check("No ADR is still only proposed", not undecided, f"decide or reject: {', '.join(undecided)}"))

    # robustness -------------------------------------------------------------------
    h = hardening_problems(text)
    checks.append(check("Adversarially hardened", not h, "; ".join(h[:3])))
    checks.append(check("Threat model", filled(secs.get("Security", ""), 40), "`## Security` needs a threat model (or a reasoned 'no new attack surface')"))
    rr = body("Rollout and Rollback")
    checks.append(check("Rollout and rollback plan", filled(secs.get("Rollout and Rollback", ""), 40) and re.search(r"roll ?back|revert", rr, re.IGNORECASE) is not None,
                        "`## Rollout and Rollback` must say how this ships and exactly how it is reverted"))
    checks.append(check("Observability", filled(secs.get("Observability", ""), 30), "`## Observability` is empty"))

    # tests ------------------------------------------------------------------------
    plan = secs.get("Test Plan", "")
    untested = [r["id"] for r in reqs if not trace.DEFERRED_RE.search(r["priority"]) and not re.search(rf"\b{re.escape(r['id'])}\b", plan)]
    checks.append(check("Test plan covers every requirement", bool(reqs) and not untested, f"no Test Plan row for: {', '.join(untested)}"))

    # honesty ----------------------------------------------------------------------
    oq = open_questions(secs.get("Open Questions", ""))
    checks.append(check("No unresolved open questions", not oq, f"{len(oq)} open: {oq[0][:60] if oq else ''}"))
    left = PLACEHOLDER_RE.findall(prose(text))
    checks.append(check("No template placeholders / TBD", not left, f"{len(left)} left, e.g. {left[0] if left else ''}"))

    # staged artifacts -------------------------------------------------------------
    notes = read(project, notes_rel(project, sdd_rel))
    checks.append(check("Design notes recorded", len(notes) > 200 and not PLACEHOLDER_RE.search(prose(notes)),
                        f"{notes_rel(project, sdd_rel)} missing, thin or still a template"))
    fs = freeze_state(project, sdd_rel)
    checks.append(check("Design frozen", fs["frozen"] and fs["intact"],
                        "design not frozen: run `design.py freeze` once the user has accepted the ADRs" if not fs["frozen"]
                        else "the SDD or an accepted ADR changed after the freeze: re-freeze (via /takshak:redesign) and regenerate the LLD"))
    ll = lld_state(project, sdd_rel)
    lld_text = read(project, ll["path"])
    checks.append(check("LLD derived from the frozen design", ll["exists"] and ll["current"] and not PLACEHOLDER_RE.search(prose(lld_text)),
                        "LLD missing, still a template, or stale against the freeze: run /takshak:lld"))
    mods = [m.strip("` ") for m in re.findall(r"^\|\s*(`[^`|]+`)\s*\|", secs.get("Module Map", ""), re.MULTILINE)]
    missing = [m for m in mods if m not in lld_text] if ll["exists"] else []
    checks.append(check("LLD specifies every module", ll["exists"] and not missing,
                        "LLD missing" if not ll["exists"] else f"modules with no spec in the LLD: {', '.join(missing[:4])}"))
    return checks


def summary(checks: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [c for c in checks if not c["ok"]]
    return {"passed": len(checks) - len(failed), "total": len(checks), "ready": not failed, "failed": failed}
