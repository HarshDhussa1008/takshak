"""Canonical documentation layout and architecture fitness rules."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from conftest import ROOT, Proj

sys.path.insert(0, str(ROOT / "hooks"))
import _fitness as fitness  # noqa: E402
from _common import Project  # noqa: E402


def tool(project: Proj, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "structure.py"), *args, "--project", str(project.root)],
                       capture_output=True, text=True, env=project.env, timeout=60, check=False)
    if check:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


def gate(project: Proj, tool_name: str, tool_input: dict) -> dict:
    return project.hook_json("approval_gate", {"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": tool_input})


def denied(result: dict) -> bool:
    return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def rules(project: Proj, *rule: dict, baseline: list[str] | None = None) -> None:
    project.write(".claude/fitness.json", {"rules": list(rule), "baseline": baseline or []})


# ------------------------------------------------------------------ documentation layout

def test_scaffold_creates_the_layout_once(project: Proj) -> None:
    out = json.loads(tool(project, "scaffold").stdout)
    for expected in ("docs/adr/", "docs/sdd/", "docs/lld/", "docs/design-notes/", "docs/implementation/", "docs/reviews/",
                     "ARCHITECTURE.md", "CONVENTIONS.md", ".claude/fitness.json"):
        assert expected in out["created"], expected
    assert json.loads(tool(project, "scaffold").stdout)["created"] == []
    assert "{{layout}}" not in (project.root / "ARCHITECTURE.md").read_text()
    assert tool(project, "check").returncode == 0  # scaffolding warns about nothing it just created, errors on nothing


def test_misplaced_documents_are_recognised_by_what_they_are(project: Proj) -> None:
    project.write("docs/notes/queue.md", "# ADR-0001: Queue over cron\n- Status: accepted\n")
    project.write("docs/random/design.md", "# SDD: Billing\n")
    project.write("docs/implementation/ok.md", "# Implementation Guide: x\n")
    out = tool(project, "check", "--json", check=False)
    assert out.returncode == 1
    problems = {p["path"]: p for p in json.loads(out.stdout)["problems"] if p["rule"] == "misplaced-doc"}
    assert "docs/adr/" in problems["docs/notes/queue.md"]["message"] and "tools/adr.py new" in problems["docs/notes/queue.md"]["message"]
    assert "docs/sdd/" in problems["docs/random/design.md"]["message"]
    assert "docs/implementation/ok.md" not in problems


def test_file_names_are_consistent(project: Proj) -> None:
    project.write("docs/sdd/Billing_Design_v2_FINAL.md", "# SDD: Billing\n")
    project.write("docs/adr/queue.md", "# ADR-0001: x\n")
    project.write("docs/adr/0002-ok-name.md", "# ADR-0002: x\n")
    out = [p for p in json.loads(tool(project, "check", "--json", check=False).stdout)["problems"] if p["rule"] == "naming"]
    assert {p["path"] for p in out} == {"docs/sdd/Billing_Design_v2_FINAL.md", "docs/adr/queue.md"}


def test_orphans_unknown_dirs_and_stray_root_docs_are_warnings(project: Proj) -> None:
    project.write("docs/sdd/billing.md", "# SDD: Billing\n")
    project.write("docs/lld/payments.md", "# LLD: Payments\n")
    project.write("docs/misc/x.txt", "x")
    project.write("design-ideas.md", "# ideas\n")
    out = tool(project, "check", "--json", check=False)
    problems = json.loads(out.stdout)["problems"]
    rules_hit = {p["rule"] for p in problems}
    assert {"orphan-doc", "unknown-docs-dir", "stray-root-doc", "missing-root-doc"} <= rules_hit
    assert all(p["severity"] == "warn" for p in problems if p["rule"] in {"orphan-doc", "unknown-docs-dir", "stray-root-doc"})
    assert out.returncode == 0  # warnings never fail the gate


def test_extra_dirs_can_be_declared(project: Proj) -> None:
    cfg = project.read(".claude/framework.json")
    cfg["layout_extra_dirs"] = ["docs/misc"]
    project.write(".claude/framework.json", cfg)
    project.write("docs/misc/x.md", "# notes\n")
    assert not [p for p in json.loads(tool(project, "check", "--json", check=False).stdout)["problems"] if p["rule"] == "unknown-docs-dir"]


def test_gate_refuses_to_file_a_document_in_the_wrong_place(project: Proj) -> None:
    wrong = {"file_path": str(project.root / "notes/queue.md"), "content": "# ADR-0001: Queue over cron\n"}
    out = gate(project, "Write", wrong)
    assert denied(out) and "wrong place" in out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "docs/adr/" in out["hookSpecificOutput"]["permissionDecisionReason"]
    right = {"file_path": str(project.root / "docs/adr/0001-queue-over-cron.md"), "content": "# ADR-0001: Queue over cron\n"}
    assert not denied(gate(project, "Write", right))
    plain = {"file_path": str(project.root / "notes/todo.md"), "content": "# my todo\n"}
    assert not denied(gate(project, "Write", plain))  # only documents that identify as takshak docs are policed


def test_structure_gate_can_be_disabled(project: Proj) -> None:
    cfg = project.read(".claude/framework.json")
    cfg["structure_gate"] = False
    project.write(".claude/framework.json", cfg)
    assert not denied(gate(project, "Write", {"file_path": str(project.root / "notes/q.md"), "content": "# ADR-0001: x\n"}))


# ------------------------------------------------------------------ fitness

@pytest.fixture
def app(project: Proj) -> Proj:
    project.write("app/__init__.py", "")
    project.write("app/api/__init__.py", "")
    project.write("app/api/routes.py", "from app.services import billing\n\ndef handler():\n    return billing.charge()\n")
    project.write("app/services/__init__.py", "")
    project.write("app/services/billing.py", "from app.db import session\n\ndef charge():\n    return session.run()\n")
    project.write("app/db/__init__.py", "")
    project.write("app/db/session.py", "import sqlalchemy\n\ndef run():\n    return 1\n")
    return project


def test_forbidden_internal_and_external_imports(app: Proj) -> None:
    rules(app, {"id": "api-no-db", "from": "app/api/", "forbid": ["app/db/", "sqlalchemy"], "description": "HTTP layer must not touch storage"})
    assert tool(app, "fitness", check=False).returncode == 0
    app.write("app/api/routes.py", "from app.db import session\nimport sqlalchemy.orm\n\ndef handler():\n    return session.run()\n")
    out = tool(app, "fitness", "--json", check=False)
    assert out.returncode == 1
    details = {v["detail"] for v in json.loads(out.stdout)["violations"]}
    assert "imports app.db" in details and "imports sqlalchemy.orm" in details


def test_layers_may_only_import_downward(app: Proj) -> None:
    rules(app, {"id": "layers", "type": "layers", "order": ["app/api/", "app/services/", "app/db/"]})
    assert tool(app, "fitness", check=False).returncode == 0
    app.write("app/db/session.py", "from app.services import billing\n\ndef run():\n    return billing\n")
    out = json.loads(tool(app, "fitness", "--json", check=False).stdout)["violations"]
    assert len(out) == 1 and out[0]["file"] == "app/db/session.py" and "upward" in out[0]["detail"]


def test_import_cycles_are_found_but_package_inits_are_not_cycles(app: Proj) -> None:
    rules(app, {"id": "no-cycles", "type": "no-cycles", "scope": "app/"})
    app.write("app/db/__init__.py", "from app.db import session\n")  # package re-exporting its own member: normal
    assert tool(app, "fitness", check=False).returncode == 0
    app.write("app/services/billing.py", "from app.api import routes\n\ndef charge():\n    return routes\n")
    out = json.loads(tool(app, "fitness", "--json", check=False).stdout)["violations"]
    assert out and "cycle" in out[0]["detail"] and "routes.py" in out[0]["detail"]


def test_file_size_limit(app: Proj) -> None:
    rules(app, {"id": "file-size", "type": "max-lines", "scope": "app/", "max": 20})
    app.write("app/services/big.py", "x = 1\n" * 40)
    out = json.loads(tool(app, "fitness", "--json", check=False).stdout)["violations"]
    assert [v["file"] for v in out] == ["app/services/big.py"]


def test_typescript_relative_imports_are_resolved(project: Proj) -> None:
    project.write("src/api/handler.ts", "import { q } from '../db/query';\nimport axios from 'axios';\n")
    project.write("src/db/query.ts", "export const q = 1;\n")
    rules(project, {"id": "api-no-db", "from": "src/api/", "forbid": ["src/db/", "axios"]})
    out = {v["detail"] for v in json.loads(tool(project, "fitness", "--json", check=False).stdout)["violations"]}
    assert out == {"imports src/db/query", "imports axios"}


def test_baseline_ratchet_tolerates_old_violations_and_fails_new_ones(app: Proj) -> None:
    app.write("app/api/legacy.py", "from app.db import session\n")
    rules(app, {"id": "api-no-db", "from": "app/api/", "forbid": ["app/db/"]})
    assert tool(app, "fitness", check=False).returncode == 1
    assert "1 existing violation" in tool(app, "fitness", "--baseline").stdout
    assert tool(app, "fitness", check=False).returncode == 0  # tolerated
    app.write("app/api/new.py", "from app.db import session\n")
    out = json.loads(tool(app, "fitness", "--json", check=False).stdout)["violations"]
    assert [v["file"] for v in out] == ["app/api/new.py"]  # only the new one
    assert len(json.loads(tool(app, "fitness", "--json", "--all", check=False).stdout)["violations"]) == 2


def test_fitness_hook_reports_the_edit_that_broke_a_rule(app: Proj) -> None:
    rules(app, {"id": "api-no-db", "from": "app/api/", "forbid": ["app/db/"], "description": "HTTP layer must not touch storage"})
    path = app.root / "app/api/routes.py"
    path.write_text("from app.db import session\n")
    out = app.hook_json("fitness_gate", {"hook_event_name": "PostToolUse", "tool_name": "Edit", "tool_input": {"file_path": str(path)}})
    text = out["hookSpecificOutput"]["additionalContext"]
    assert "api-no-db" in text and "imports app.db" in text and "do not edit the rule" in text
    ok = app.root / "app/services/billing.py"
    assert app.hook("fitness_gate", {"tool_name": "Edit", "tool_input": {"file_path": str(ok)}}).stdout.strip() == ""


def test_fitness_hook_is_silent_without_rules_and_when_disabled(app: Proj) -> None:
    path = app.root / "app/api/routes.py"
    assert app.hook("fitness_gate", {"tool_name": "Edit", "tool_input": {"file_path": str(path)}}).stdout.strip() == ""
    rules(app, {"id": "r", "from": "app/api/", "forbid": ["app/services/"]})
    cfg = app.read(".claude/framework.json")
    cfg["fitness_gate"] = False
    app.write(".claude/framework.json", cfg)
    assert app.hook("fitness_gate", {"tool_name": "Edit", "tool_input": {"file_path": str(path)}}).stdout.strip() == ""


def test_matching_semantics() -> None:
    assert fitness.matches("app.db.session", "app/db/")
    assert fitness.matches("sqlalchemy.orm", "sqlalchemy")
    assert fitness.matches("@scope/pkg/sub", "@scope/pkg")
    assert not fitness.matches("app.dbx.session", "app/db/")
    assert not fitness.matches("requests_mock", "requests")


def test_stop_hook_reports_violations_in_changed_files(app: Proj) -> None:
    app.init_git()
    app.write(".claude/.gitignore", "*\n")
    app.git("add", "-A")
    app.git("commit", "-qm", "base")
    rules(app, {"id": "api-no-db", "from": "app/api/", "forbid": ["app/db/"]})
    (app.root / "app/api/routes.py").write_text("from app.db import session\n")
    app.hook("reconcile_tracker", {"tool_name": "Edit", "tool_input": {"file_path": str(app.root / "app/api/routes.py")}})
    out = app.hook_json("stop_checklist", {"hook_event_name": "Stop"})
    assert "architecture-rule violation" in out.get("systemMessage", "")
    assert Project(app.root).enabled
