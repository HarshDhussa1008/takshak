"""Takshak staged design -- the deterministic half of /takshak:design and /takshak:lld.

    run.sh tools/design.py scaffold  --project DIR --title T      # notes + SDD skeletons in the canonical places
    run.sh tools/design.py status    --project DIR [--sdd PATH] [--json]
    run.sh tools/design.py freeze    --project DIR [--sdd PATH]   # lock the HLD + accepted ADRs; LLD derives from this
    run.sh tools/design.py stamp-lld --project DIR [--sdd PATH]   # record which freeze the LLD was derived from
    run.sh tools/design.py readiness --project DIR [--sdd PATH] [--json]   # exit 1 until implementation may start
    run.sh tools/design.py pack      --project DIR [--sdd PATH]   # one-file reviewer pack: docs/reviews/<slug>.html

Stages, in order: discovery notes -> requirements -> ADRs -> HLD (SDD) -> hardening -> FREEZE -> LLD ->
readiness -> reviewer pack -> the user's "approved". `readiness` is the gate `/takshak:breakdown` applies.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import _adr as adr  # noqa: E402
import _design as design  # noqa: E402
import _trace as trace  # noqa: E402
from _common import Project, read_json  # noqa: E402

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def resolve_sdd(project: Project, override: str | None) -> str | None:
    if override:
        return Path(override).as_posix()
    found = trace.sdd_file(project)
    return found.relative_to(project.root).as_posix() if found else None


def need_sdd(project: Project, args: argparse.Namespace) -> str:
    sdd = resolve_sdd(project, getattr(args, "sdd", None))
    if not sdd:
        print("no SDD found: pass --sdd or run `design.py scaffold --title ...`", file=sys.stderr)
        sys.exit(2)
    return sdd


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "feature"


# ------------------------------------------------------------------------ commands

def cmd_scaffold(args: argparse.Namespace) -> int:
    project = Project(Path(args.project))
    slug, cfg = slugify(args.title), project.config()
    sdd_rel = f"{str(cfg.get('sdd_path') or 'docs/sdd').strip('/')}/{slug}.md"
    created = []
    for rel, template in ((sdd_rel, "SDD.md.template"), (design.notes_rel(project, sdd_rel), "DESIGN_NOTES.md.template")):
        path = project.root / rel
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((TEMPLATES / template).read_text(encoding="utf-8").replace("{{feature title}}", args.title), encoding="utf-8")
        created.append(rel)
    print(json.dumps({"sdd": sdd_rel, "notes": design.notes_rel(project, sdd_rel), "lld": design.lld_rel(project, sdd_rel), "created": created}, indent=2))
    return 0


def stage_of(project: Project, sdd: str, checks: list[dict]) -> str:
    """discovery -> hld -> hardened -> frozen -> lld -> ready (or design-changed-after-freeze)."""
    if not design.read(project, sdd):
        return "discovery"
    failed = {c["check"] for c in checks if not c["ok"]}
    if not failed:
        return "ready"
    fs = design.freeze_state(project, sdd)
    if not fs["frozen"]:
        return "hld" if ("Adversarially hardened" in failed or "Decisions recorded as ADRs" in failed) else "hardened"
    if not fs["intact"]:
        return "design-changed-after-freeze"
    if {"LLD derived from the frozen design", "LLD specifies every module"} & failed:
        return "frozen"
    return "lld"


def cmd_status(args: argparse.Namespace) -> int:
    project = Project(Path(args.project))
    sdd = need_sdd(project, args)
    checks = design.readiness(project, sdd)
    info = {"sdd": sdd, "stage": stage_of(project, sdd, checks), "freeze": design.freeze_state(project, sdd),
            "lld": design.lld_state(project, sdd), **design.summary(checks)}
    if args.json:
        print(json.dumps(info, indent=2))
    else:
        print(f"{sdd}: stage {info['stage']} -- readiness {info['passed']}/{info['total']}")
    return 0


def cmd_freeze(args: argparse.Namespace) -> int:
    project = Project(Path(args.project))
    sdd = need_sdd(project, args)
    secs = design.sections(design.read(project, sdd))
    problems = []
    if not trace.parse_requirements(design.read(project, sdd)):
        problems.append("no requirement IDs")
    if not any(adr.is_live(a) for a in adr.load_all(project)):
        problems.append("no accepted ADR")
    if design.hardening_problems(design.read(project, sdd)):
        problems.append("not hardened (Risk Register incomplete)")
    if design.open_questions(secs.get("Open Questions", "")):
        problems.append("open questions remain")
    if problems:
        print("cannot freeze yet: " + "; ".join(problems), file=sys.stderr)
        return 1
    state = design.freeze_state(project, sdd)
    if state["frozen"] and not state["intact"] and not (args.reason or "").strip():
        print("the design changed after it was frozen: re-freezing needs --reason (e.g. the design change id, DC-1) so the change is on the record; "
              "a substantive change goes through /takshak:redesign", file=sys.stderr)
        return 1
    rec = design.freeze(project, sdd, args.reason or "")
    print(f"frozen {sdd} at {rec['hash']} ({len(rec['adrs'])} accepted ADR(s)); derive the LLD with /takshak:lld")
    return 0


def cmd_stamp(args: argparse.Namespace) -> int:
    project = Project(Path(args.project))
    sdd = need_sdd(project, args)
    stamped = design.stamp_lld(project, sdd)
    if not stamped:
        print("cannot stamp: the design is not frozen or the LLD file does not exist", file=sys.stderr)
        return 1
    print(f"LLD stamped frozen-from {stamped}")
    return 0


def cmd_readiness(args: argparse.Namespace) -> int:
    project = Project(Path(args.project))
    sdd = need_sdd(project, args)
    checks = design.readiness(project, sdd)
    info = design.summary(checks)
    if args.json:
        print(json.dumps({"sdd": sdd, **info, "checks": checks}, indent=2))
    else:
        for c in checks:
            print(f"[{'ok' if c['ok'] else 'FAIL'}] {c['check']}" + (f" -- {c['detail']}" if not c["ok"] else ""))
        print(f"readiness {info['passed']}/{info['total']}: " + ("READY for breakdown" if info["ready"] else "NOT ready"))
    return 0 if info["ready"] else 1


# ------------------------------------------------------------------------ reviewer pack

def inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)


def md_to_html(md: str) -> str:
    """Small, safe renderer for the subset SDDs use: fenced code (mermaid kept for rendering),
    tables, bullets, paragraphs. Everything is escaped before any markup is added."""
    out: list[str] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("```"):
            lang = ln.strip()[3:].strip()
            j, block = i + 1, []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                block.append(lines[j])
                j += 1
            code = html.escape("\n".join(block))
            out.append(f'<pre class="mermaid">{code}</pre>' if lang == "mermaid" else f"<pre><code>{code}</code></pre>")
            i = j + 1
            continue
        if ln.strip().startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(trace.split_row(lines[i]))
                i += 1
            body = [r for r in rows if not set("".join(r)) <= set("-: ")]
            if body:
                head, rest = body[0], body[1:]
                out.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
                           + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rest) + "</tbody></table>")
            continue
        if re.match(r"^\s*[-*]\s+", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+(\[[ xX]\]\s*)?", "", lines[i]))
                i += 1
            out.append("<ul>" + "".join(f"<li>{inline(t)}</li>" for t in items) + "</ul>")
            continue
        if ln.strip():
            para = [ln]
            i += 1
            while i < len(lines) and lines[i].strip() and not lines[i].lstrip().startswith(("|", "-", "*", "```")):
                para.append(lines[i])
                i += 1
            out.append(f"<p>{inline(' '.join(para))}</p>")
            continue
        i += 1
    return "\n".join(out)


PACK_CSS = """
:root{--bg:#fff;--fg:#1c1f23;--mut:#5b6470;--line:#d9dde3;--ok:#1a7f4b;--bad:#b3261e;--warn:#9a6700;--card:#f6f8fa;--acc:#2457c5}
@media (prefers-color-scheme:dark){:root{--bg:#14171b;--fg:#e6e8eb;--mut:#9aa4b2;--line:#2a2f36;--ok:#4cc38a;--bad:#ff7b72;--warn:#e3b341;--card:#1b2026;--acc:#79a6ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 64px}h1{font-size:1.7rem;margin:.2em 0}h2{font-size:1.25rem;margin:2em 0 .5em;border-bottom:1px solid var(--line);padding-bottom:.25em}
h3{font-size:1.05rem;margin:1.2em 0 .3em}.mut{color:var(--mut)}table{border-collapse:collapse;width:100%;margin:.6em 0;font-size:.92rem;display:block;overflow-x:auto}
th,td{border:1px solid var(--line);padding:6px 9px;text-align:left;vertical-align:top}th{background:var(--card)}code{background:var(--card);padding:1px 4px;border-radius:4px;font-size:.9em}
pre{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;overflow-x:auto}.chips{display:flex;flex-wrap:wrap;gap:8px;margin:.6em 0}
.chip{border:1px solid var(--line);border-radius:999px;padding:3px 12px;font-size:.88rem;background:var(--card)}.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.card{border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:.8em 0;background:var(--card)}.decide{border-color:var(--acc)}
"""


def build_pack(project: Project, sdd: str) -> str:
    text = design.read(project, sdd)
    secs = design.sections(text)
    checks = design.readiness(project, sdd)
    info = design.summary(checks)
    report = trace.build(project, sdd)
    title = (re.match(r"#\s*(?:SDD:\s*)?(.+)", text) or [None, "Design"])[1]
    fs = design.freeze_state(project, sdd)
    esc = html.escape

    def sec(name: str, heading: str | None = None) -> str:
        body = secs.get(name, "").strip()
        return f"<h2>{esc(heading or name)}</h2>\n{md_to_html(body)}" if body else ""

    chips = [
        f'<span class="chip {"ok" if info["ready"] else "bad"}">Readiness {info["passed"]}/{info["total"]}</span>',
        f'<span class="chip {"ok" if fs["frozen"] and fs["intact"] else "warn"}">Design {"frozen" if fs["frozen"] and fs["intact"] else "not frozen"}</span>',
        f'<span class="chip">{len(trace.parse_requirements(text))} requirements</span>',
        f'<span class="chip">{sum(1 for a in adr.load_all(project) if adr.is_live(a))} accepted ADRs</span>',
    ]
    adr_cards = []
    for a in adr.load_all(project):
        opts = re.findall(r"^###\s+(.+)$", adr.section(a["text"], "Options considered"), re.MULTILINE)
        cls = "card decide" if a["status"].lower() == "proposed" else "card"
        adr_cards.append(
            f'<div class="{cls}"><strong>ADR-{a["number"]:04d} &middot; {esc(a["title"])}</strong> <span class="mut">({esc(a["status"])})</span>'
            f'<p class="mut">Considered: {esc("; ".join(opts) or "-")}</p>{md_to_html(adr.section(a["text"], "Decision"))}'
            f'<p class="mut">Consequences</p>{md_to_html(adr.section(a["text"], "Consequences"))}</div>')
    checklist = "".join(f'<tr><td class="{"ok" if c["ok"] else "bad"}">{"pass" if c["ok"] else "FAIL"}</td><td>{esc(c["check"])}</td><td>{esc(c["detail"])}</td></tr>' for c in checks)
    decide = [f"ADR-{a['number']:04d} {a['title']}" for a in adr.load_all(project) if a["status"].lower() == "proposed"]
    oq = design.open_questions(secs.get("Open Questions", ""))
    ask = decide + oq
    ask_html = ("<ul>" + "".join(f"<li>{esc(x)}</li>" for x in ask) + "</ul>") if ask else '<p class="ok">Nothing is waiting on a decision.</p>'
    trace_html = md_to_html(trace.render_markdown(report))
    refreezes = read_json(project.root / design.freeze_rel(sdd)).get("refreezes") or []
    refreeze_html = ("<h2>Changes after the freeze</h2><table><thead><tr><th>When</th><th>Reason</th></tr></thead><tbody>"
                     + "".join(f"<tr><td>{esc(str(r.get('at', '')))}</td><td>{esc(str(r.get('reason') or '(none given)'))}</td></tr>" for r in refreezes)
                     + "</tbody></table>") if refreezes else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Design review: {esc(title)}</title><style>{PACK_CSS}</style></head><body><main>
<p class="mut">Design review pack &middot; generated from <code>{esc(sdd)}</code> &middot; the document is the source of truth, this page is a view of it</p>
<h1>{esc(title)}</h1><div class="chips">{"".join(chips)}</div>
<div class="card decide"><strong>Decisions needed from you</strong>{ask_html}
<p class="mut">To approve the design together with the task plan, reply <code>approved</code> after <code>/takshak:breakdown</code>.</p></div>
{sec("Overview")}{sec("Goals")}{sec("Non-Goals")}{sec("Requirements")}
<h2>Requirement coverage</h2>{trace_html}
<h2>Decisions</h2>{"".join(adr_cards) or '<p class="mut">No ADRs recorded.</p>'}
{sec("Architecture")}{sec("Key Flows")}{sec("Module Map")}{sec("API Contracts")}{sec("Data Models")}
{refreeze_html}{sec("Security", "Security and threat model")}{sec("Rollout and Rollback")}{sec("Observability")}{sec("Risk Register")}
<h2>Readiness checklist</h2><table><thead><tr><th>Result</th><th>Check</th><th>Detail</th></tr></thead><tbody>{checklist}</tbody></table>
</main>
<script src="https://cdnjs.cloudflare.com/ajax/libs/mermaid/10.9.2/mermaid.min.js"></script>
<script>try{{mermaid.initialize({{startOnLoad:true,theme:matchMedia('(prefers-color-scheme: dark)').matches?'dark':'default'}})}}catch(e){{}}</script>
</body></html>"""


def cmd_pack(args: argparse.Namespace) -> int:
    project = Project(Path(args.project))
    sdd = need_sdd(project, args)
    rel = f"{str(project.config().get('review_path') or 'docs/reviews').strip('/')}/{design.slug_of(sdd)}.html"
    path = project.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_pack(project, sdd), encoding="utf-8")
    print(rel)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("scaffold", cmd_scaffold), ("status", cmd_status), ("freeze", cmd_freeze), ("stamp-lld", cmd_stamp),
                       ("readiness", cmd_readiness), ("pack", cmd_pack)):
        p = sub.add_parser(name)
        p.add_argument("--project", default=".")
        p.set_defaults(func=func)
        if name == "scaffold":
            p.add_argument("--title", required=True)
        else:
            p.add_argument("--sdd")
        if name in ("status", "readiness"):
            p.add_argument("--json", action="store_true")
        if name == "freeze":
            p.add_argument("--reason", help="required when re-freezing a design that changed after its last freeze")
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
