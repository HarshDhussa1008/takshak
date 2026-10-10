"""The newcomer walkthrough: scaffolded by reconcile, checked in code."""

from __future__ import annotations

import json
import re
import subprocess
import sys

from conftest import ROOT, Proj


def tool(project: Proj, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(ROOT / "tools" / "onboard.py"), *args, "--project", str(project.root)],
                          capture_output=True, text=True, env=project.env, timeout=60, check=False)


def make_code(project: Proj) -> None:
    project.write("app.py", "\n".join(f"line {i}" for i in range(1, 41)) + "\n")


def fill(project: Proj, rel: str) -> str:
    text = (project.root / rel).read_text(encoding="utf-8")
    text = text.replace("{{N of M modules}}", "1 of 1 modules").replace("{{ISO date}}", "2026-01-01")
    text = re.sub(r"\{\{[^}\n]*\}\}", "filled", text)
    text = text.replace("Verified by running? filled", "Verified by running? no, read only")
    text = text.replace("| filled | filled | `filled` |", "| alpha | the first thing | `app.py:1` |\n| beta | the second thing | `app.py:2` |")
    text = text.replace("1. filled\n2. filled", "1. What runs first? (`app.py:3`)\n2. Change `app.py:4` and predict the effect.")
    cites = " ".join(f"`app.py:{n}`" for n in range(5, 15))
    return text + f"\nCitations: {cites}\n"


def scaffold(project: Proj) -> str:
    out = json.loads(tool(project, "scaffold", "--name", "Demo App").stdout)
    return out["created"]


def test_scaffold_goes_to_the_canonical_folder(project: Proj) -> None:
    assert scaffold(project) == "docs/onboarding/demo-app.md"
    assert tool(project, "scaffold", "--name", "Demo App").stdout.strip().startswith('{"exists"')


def test_unfilled_template_fails_with_reasons(project: Proj) -> None:
    make_code(project)
    scaffold(project)
    r = tool(project, "check")
    assert r.returncode == 1
    assert "unfilled placeholder" in r.stdout and "citations" in r.stdout


def test_filled_guide_passes(project: Proj) -> None:
    make_code(project)
    rel = scaffold(project)
    (project.root / rel).write_text(fill(project, rel), encoding="utf-8")
    r = tool(project, "check")
    assert r.returncode == 0, r.stdout


def test_bad_citation_is_caught(project: Proj) -> None:
    make_code(project)
    rel = scaffold(project)
    text = fill(project, rel) + "\nSee `app.py:999` and `ghost.py:1`.\n"
    (project.root / rel).write_text(text, encoding="utf-8")
    r = tool(project, "check")
    assert r.returncode == 1
    assert "app.py:999" in r.stdout and "ghost.py:1" in r.stdout


def test_missing_diagram_is_caught(project: Proj) -> None:
    make_code(project)
    rel = scaffold(project)
    text = fill(project, rel).replace("sequenceDiagram", "gantt")
    (project.root / rel).write_text(text, encoding="utf-8")
    assert "sequenceDiagram" in tool(project, "check").stdout


def test_misfiled_guide_is_flagged_by_structure(project: Proj) -> None:
    project.write("docs/guides/guide.md", "# Onboarding Guide: x\n\nbody\n")
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "structure.py"), "check", "--json", "--project", str(project.root)],
                       capture_output=True, text=True, env=project.env, timeout=60, check=False)
    assert "onboarding guide" in r.stdout and "docs/onboarding" in r.stdout
